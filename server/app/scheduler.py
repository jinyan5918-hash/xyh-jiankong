import os
import random
import threading
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, joinedload

from .database import SessionLocal
from .models import CommentAlert, MonitorRecord, MonitorTask, ReachAlert, User
from .wecom import pick_webhook_for_user, push_comment_alert, push_reach_alert


def _load_pw_fetch_metrics():
    """仅加载 Playwright 抓取（DOUYIN_USE_PLAYWRIGHT=1）。机房场景应优先走此路径。"""
    root = Path(__file__).resolve().parents[2]
    use_pw = os.getenv("DOUYIN_USE_PLAYWRIGHT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not use_pw:
        return None
    for filename in ("douyin_fetch_playwright.py",):
        root_file = root / filename
        if not root_file.exists():
            continue
        import importlib.util

        spec = importlib.util.spec_from_file_location(root_file.stem, str(root_file))
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            continue
        fn = getattr(module, "fetch_metrics", None)
        if fn is not None:
            return fn
        fn2 = getattr(module, "fetch_likes", None)
        if fn2 is not None:

            def _metrics(url: str, insecure_ssl: bool = True):
                return {
                    "likes": int(fn2(url, insecure_ssl=insecure_ssl)),
                    "comment_count": None,
                    "latest_comment": None,
                }

            return _metrics
    print(
        "[scheduler] 已设置 DOUYIN_USE_PLAYWRIGHT=1 但 Playwright 模块未就绪，将仅用 HTTP（易被抖音 403）"
    )
    return None


def _is_likely_http_403(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 403:
        return True
    s = str(exc).lower()
    return "403" in s or "forbidden" in s


def _load_http_fetch_metrics():
    """兜底 HTTP 抓取：当 Playwright 单次失败时回退，减少整站失败噪声。"""
    root = Path(__file__).resolve().parents[2]
    for filename in ("douyin_fetch.py", "douyin_monitor_gui.py"):
        root_file = root / filename
        if not root_file.exists():
            continue
        import importlib.util

        spec = importlib.util.spec_from_file_location(root_file.stem, str(root_file))
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            continue
        fn = getattr(module, "fetch_metrics", None)
        if fn is not None:
            return fn
        fn2 = getattr(module, "fetch_likes", None)
        if fn2 is not None:
            def _metrics(url: str, insecure_ssl: bool = True):
                return {"likes": int(fn2(url, insecure_ssl=insecure_ssl)), "comment_count": None, "latest_comment": None}

            return _metrics
    return None


@dataclass
class TaskRuntimeState:
    next_run_at: float = 0.0
    fail_count: int = 0
    last_error: str = ""
    last_likes: int | None = None
    last_run_at: float = 0.0
    # 上次已向企微/提醒表推送时的点赞数；低于目标后清零，再次达标或继续上涨会再次推送
    last_push_likes: int | None = None
    last_comment_count: int | None = None
    last_comment_sig: str | None = None


class MonitorScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._states: dict[int, TaskRuntimeState] = {}
        self._fetch_metrics_pw = _load_pw_fetch_metrics()
        self._fetch_metrics_http = _load_http_fetch_metrics()
        if self._fetch_metrics_pw is None and self._fetch_metrics_http is not None:
            print(
                "[scheduler] 提示：未启用或未能加载 Playwright，仅使用 HTTP 抓取；"
                "机房 IP 易被抖音返回 403。请在环境变量设置 DOUYIN_USE_PLAYWRIGHT=1、"
                "playwright install chromium，并配置 DOUYIN_COOKIE 或 DOUYIN_PROXY_POOL。"
            )
        self.interval_min_sec = int(os.getenv("SCHED_INTERVAL_MIN_SEC", "180"))
        self.interval_max_sec = int(os.getenv("SCHED_INTERVAL_MAX_SEC", "480"))
        self.cooldown_min_sec = int(os.getenv("SCHED_COOLDOWN_MIN_SEC", "900"))
        self.cooldown_max_sec = int(os.getenv("SCHED_COOLDOWN_MAX_SEC", "1800"))
        self.stagger_sec_max = float(os.getenv("SCHED_STAGGER_SEC_MAX", "0.25"))

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            self._running = False
            self._stop_event.set()
            return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            states = {
                task_id: {
                    "next_run_at": s.next_run_at,
                    "fail_count": s.fail_count,
                    "last_error": s.last_error,
                    "last_likes": s.last_likes,
                    "last_comment_count": s.last_comment_count,
                    "last_comment_sig": s.last_comment_sig,
                    "last_run_at": s.last_run_at,
                    "last_push_likes": s.last_push_likes,
                }
                for task_id, s in self._states.items()
            }
            return {"running": self._running, "states": states}

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.time()
            db: Session = SessionLocal()
            try:
                tasks = (
                    db.query(MonitorTask)
                    .join(User, MonitorTask.user_id == User.id)
                    .options(joinedload(MonitorTask.user))
                    .filter(
                        MonitorTask.enabled.is_(True),
                        MonitorTask.task_paused.is_(False),
                        User.monitoring_active.is_(True),
                        User.monitoring_paused.is_(False),
                    )
                    .all()
                )
                tasks = list(tasks)
                random.shuffle(tasks)
                for task in tasks:
                    tid = int(task.id)
                    state = self._states.setdefault(tid, TaskRuntimeState())
                    if state.next_run_at and now < state.next_run_at:
                        continue
                    # 必须在独立 Session 内重新加载任务：外层 query 的 ORM 对象不能跨 Session merge
                    self._run_task(tid, state)
                    if self.stagger_sec_max > 0:
                        time.sleep(random.uniform(0, self.stagger_sec_max))
            finally:
                db.close()
            time.sleep(1.0)

    def _run_task(self, task_id: int, state: TaskRuntimeState) -> None:
        state.last_run_at = time.time()
        db: Session = SessionLocal()
        try:
            task = (
                db.query(MonitorTask)
                .options(joinedload(MonitorTask.user))
                .filter(MonitorTask.id == task_id)
                .first()
            )
            if task is None:
                return
            if not bool(task.enabled) or bool(task.task_paused):
                return
            if self._fetch_metrics_pw is None and self._fetch_metrics_http is None:
                raise RuntimeError("fetch_metrics not available")
            metrics: dict[str, Any] | None = None
            pw_err: BaseException | None = None
            if self._fetch_metrics_pw is not None:
                try:
                    metrics = self._fetch_metrics_pw(task.video_url, insecure_ssl=True)
                except Exception as e_pw:
                    pw_err = e_pw
                    print(f"[scheduler] task={task.id} Playwright失败，回退HTTP: {e_pw}")
            if metrics is None:
                if self._fetch_metrics_http is None:
                    if pw_err is not None:
                        raise pw_err
                    raise RuntimeError("fetch_metrics not available")
                try:
                    metrics = self._fetch_metrics_http(task.video_url, insecure_ssl=True) or {}
                except Exception as e_http:
                    if _is_likely_http_403(e_http):
                        raise RuntimeError(
                            "抖音返回 HTTP 403（机房/高频请求常被风控）。"
                            "请管理员在 jiankong-api 环境变量中配置至少一项："
                            "DOUYIN_COOKIE=（从浏览器打开抖音后复制整段 Cookie）；"
                            "或 DOUYIN_PROXY_POOL=（可用代理 URL，逗号分隔）；"
                            "并确保 DOUYIN_USE_PLAYWRIGHT=1 且已执行 playwright install chromium，"
                            "然后 systemctl restart jiankong-api。"
                            f" 原始错误：{e_http}"
                        ) from e_http
                    if pw_err is not None:
                        raise RuntimeError(
                            f"Playwright 失败：{pw_err}；HTTP 回退失败：{e_http}"
                        ) from e_http
                    raise
            likes = int(metrics.get("likes") or 0)
            comment_count = metrics.get("comment_count")
            latest_comment = metrics.get("latest_comment")
            try:
                comment_count_int = int(comment_count) if comment_count is not None else None
            except Exception:
                comment_count_int = None
            state.last_likes = likes
            state.last_comment_count = comment_count_int
            state.last_error = ""
            state.fail_count = 0
            db.add(
                MonitorRecord(
                    task_id=task.id,
                    likes=likes,
                    comment_count=comment_count_int,
                    latest_comment=(str(latest_comment).strip()[:400] if latest_comment else None),
                    success=True,
                    error_message="",
                )
            )
            db.commit()
            # 1) 点赞增长步长提醒（持久化 last_notified_likes，避免重启重复提醒）
            step = int(getattr(task, "notify_step_likes", 0) or 0)
            if step > 0:
                ln = getattr(task, "last_notified_likes", None)
                if ln is None:
                    task.last_notified_likes = likes
                    db.add(task)
                    db.commit()
                elif likes - int(ln) >= step:
                    task.last_notified_likes = likes
                    db.add(task)
                    db.add(
                        ReachAlert(
                            user_id=task.user_id,
                            task_id=task.id,
                            task_name=task.name,
                            likes=likes,
                            target_likes=step,
                        )
                    )
                    db.commit()
                    print(f"[scheduler] task={task.id} notify step, likes={likes}, step={step}")
                    hook = pick_webhook_for_user(task.user.wecom_webhook_url)
                    if hook:
                        try:
                            push_reach_alert(
                                hook,
                                task_id=task.id,
                                task_name=task.name,
                                likes=likes,
                                target_likes=step,
                                video_url=task.video_url,
                            )
                        except Exception as ex:
                            print(f"[wecom] push failed task={task.id}: {ex}")

            # 2) 新评论提醒（以评论数增长为主，辅以签名去重）
            if comment_count_int is not None:
                prev = getattr(task, "last_comment_count", None)
                sig = (str(latest_comment).strip()[:140] if latest_comment else None)
                if prev is None:
                    task.last_comment_count = comment_count_int
                    task.last_comment_sig = sig
                    db.add(task)
                    db.commit()
                elif comment_count_int > int(prev):
                    # 去重：若 sig 未变化且增长很小，仍提醒一次即可
                    task.last_comment_count = comment_count_int
                    task.last_comment_sig = sig
                    db.add(task)
                    db.add(
                        CommentAlert(
                            user_id=task.user_id,
                            task_id=task.id,
                            task_name=task.name,
                            comment_count=comment_count_int,
                            comment_snippet=sig,
                        )
                    )
                    db.commit()
                    print(f"[scheduler] task={task.id} notify comment, count={comment_count_int}")
                    hook = pick_webhook_for_user(task.user.wecom_webhook_url)
                    if hook:
                        try:
                            push_comment_alert(
                                hook,
                                task_id=task.id,
                                task_name=task.name,
                                comment_count=comment_count_int,
                                comment_snippet=sig,
                                video_url=task.video_url,
                            )
                        except Exception as ex:
                            print(f"[wecom] comment push failed task={task.id}: {ex}")
            u = task.user
            imin = (
                u.interval_min_sec
                if u.interval_min_sec is not None
                else self.interval_min_sec
            )
            imax = (
                u.interval_max_sec
                if u.interval_max_sec is not None
                else self.interval_max_sec
            )
            if imin > imax:
                imin, imax = imax, imin
            state.next_run_at = time.time() + random.uniform(float(imin), float(imax))
        except Exception as e:
            state.last_error = str(e)
            state.fail_count += 1
            db.add(
                MonitorRecord(
                    task_id=task.id,
                    likes=None,
                    success=False,
                    error_message=str(e),
                )
            )
            db.commit()
            if state.fail_count >= 3:
                # 连续失败进入更长冷却，避免持续触发平台风控。
                state.next_run_at = time.time() + random.uniform(
                    self.cooldown_min_sec, self.cooldown_max_sec
                )
            else:
                backoff = min(900, (2 ** state.fail_count) * 60)
                state.next_run_at = time.time() + backoff
        finally:
            db.close()


scheduler = MonitorScheduler()
