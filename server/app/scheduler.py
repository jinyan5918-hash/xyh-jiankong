import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, joinedload

from .database import SessionLocal
from .models import MonitorRecord, MonitorTask, ReachAlert, User
from .wecom import pick_webhook_for_user, push_reach_alert


def _load_fetch_likes():
    # 可通过 DOUYIN_USE_PLAYWRIGHT=1 启用真浏览器路径（见 docs/DOUYIN_FETCH_EVOLUTION.md）
    root = Path(__file__).resolve().parents[2]
    use_pw = os.getenv("DOUYIN_USE_PLAYWRIGHT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    candidates = (
        ("douyin_fetch_playwright.py", "douyin_fetch.py", "douyin_monitor_gui.py")
        if use_pw
        else ("douyin_fetch.py", "douyin_monitor_gui.py")
    )
    for filename in candidates:
        root_file = root / filename
        if not root_file.exists():
            continue
        import importlib.util

        mod_name = root_file.stem
        spec = importlib.util.spec_from_file_location(mod_name, str(root_file))
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            continue
        fn = getattr(module, "fetch_likes", None)
        if fn is not None:
            return fn
    # 声明了 Playwright 但加载失败时回退 HTTP，避免整站监控停摆
    if use_pw:
        for filename in ("douyin_fetch.py", "douyin_monitor_gui.py"):
            root_file = root / filename
            if not root_file.exists():
                continue
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                root_file.stem, str(root_file)
            )
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue
            fn = getattr(module, "fetch_likes", None)
            if fn is not None:
                print(
                    "[scheduler] DOUYIN_USE_PLAYWRIGHT=1 但 Playwright 未就绪，已回退 douyin_fetch.py"
                )
                return fn
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


class MonitorScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._states: dict[int, TaskRuntimeState] = {}
        self._fetch_likes = _load_fetch_likes()
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
                        User.monitoring_active.is_(True),
                        User.monitoring_paused.is_(False),
                    )
                    .all()
                )
                tasks = list(tasks)
                random.shuffle(tasks)
                for task in tasks:
                    state = self._states.setdefault(task.id, TaskRuntimeState())
                    if state.next_run_at and now < state.next_run_at:
                        continue
                    self._run_task(task, state)
                    if self.stagger_sec_max > 0:
                        time.sleep(random.uniform(0, self.stagger_sec_max))
            finally:
                db.close()
            time.sleep(1.0)

    def _run_task(self, task: MonitorTask, state: TaskRuntimeState) -> None:
        state.last_run_at = time.time()
        db: Session = SessionLocal()
        try:
            if not self._fetch_likes:
                raise RuntimeError("fetch_likes not available")
            likes = int(self._fetch_likes(task.video_url, insecure_ssl=True))
            state.last_likes = likes
            state.last_error = ""
            state.fail_count = 0
            if likes < task.target_likes:
                state.last_push_likes = None
            db.add(
                MonitorRecord(
                    task_id=task.id,
                    likes=likes,
                    success=True,
                    error_message="",
                )
            )
            db.commit()
            if likes >= task.target_likes:
                should_notify = state.last_push_likes is None or likes > state.last_push_likes
                if should_notify:
                    state.last_push_likes = likes
                    db.add(
                        ReachAlert(
                            user_id=task.user_id,
                            task_id=task.id,
                            task_name=task.name,
                            likes=likes,
                            target_likes=task.target_likes,
                        )
                    )
                    db.commit()
                    print(
                        f"[scheduler] task={task.id} notify reach, likes={likes}, target={task.target_likes}"
                    )
                    hook = pick_webhook_for_user(task.user.wecom_webhook_url)
                    if hook:
                        try:
                            push_reach_alert(
                                hook,
                                task_id=task.id,
                                task_name=task.name,
                                likes=likes,
                                target_likes=task.target_likes,
                                video_url=task.video_url,
                            )
                        except Exception as ex:
                            print(f"[wecom] push failed task={task.id}: {ex}")
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
