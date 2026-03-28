import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set


DIGG_PATTERN = re.compile(r'"digg_count"\s*:\s*(\d+)')


@dataclass
class VideoConfig:
    name: str
    url: str
    target_likes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="监控多个抖音视频点赞量，达到阈值时发出系统通知。"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="配置文件路径，默认 ./config.json",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> tuple[int, List[VideoConfig]]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"未找到配置文件: {config_path}，请先复制 config.example.json 为 config.json 并填写。"
        )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    interval = int(data.get("check_interval_seconds", 60))
    videos_raw = data.get("videos", [])
    if not videos_raw:
        raise ValueError("配置错误: videos 不能为空。")

    videos: List[VideoConfig] = []
    for i, item in enumerate(videos_raw, start=1):
        name = str(item.get("name", f"视频{i}")).strip()
        url = str(item.get("url", "")).strip()
        target_likes = int(item.get("target_likes", 0))
        if not url:
            raise ValueError(f"配置错误: 第{i}个视频缺少 url。")
        if target_likes <= 0:
            raise ValueError(f"配置错误: 第{i}个视频的 target_likes 必须大于 0。")
        videos.append(VideoConfig(name=name, url=url, target_likes=target_likes))

    return interval, videos


def send_macos_notification(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False)


def short_num(num: int) -> str:
    if num >= 100000000:
        return f"{num / 100000000:.2f}亿"
    if num >= 10000:
        return f"{num / 10000:.2f}万"
    return str(num)


def fetch_html(url: str, timeout: int = 45) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    req = urllib.request.Request(url=url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="ignore")


def extract_likes_from_html(url: str) -> Optional[int]:
    content = fetch_html(url)

    matches = DIGG_PATTERN.findall(content)
    if not matches:
        return None

    # 页面可能有多处 digg_count，取最大值更稳妥。
    likes = max(int(x) for x in matches)
    return likes


def monitor_loop(interval: int, videos: List[VideoConfig]) -> None:
    reached_once: Set[str] = set()
    last_value: Dict[str, int] = {}

    while True:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] 开始新一轮检测...")
        for video in videos:
            try:
                likes = extract_likes_from_html(video.url)
                if likes is None:
                    print(f"- {video.name}: 未解析到点赞数（可能触发风控或页面结构变化）")
                    continue

                last_value[video.url] = likes
                progress = f"{short_num(likes)}/{short_num(video.target_likes)}"
                print(f"- {video.name}: 当前点赞 {likes} ({progress})")

                if likes >= video.target_likes and video.url not in reached_once:
                    reached_once.add(video.url)
                    msg = (
                        f"{video.name} 点赞达到 {short_num(likes)}，"
                        f"已超过目标 {short_num(video.target_likes)}"
                    )
                    print(f"  -> 触发提醒: {msg}")
                    send_macos_notification("抖音点赞监控提醒", msg)
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"- {video.name}: 网络错误: {e}")
            except Exception as e:
                print(f"- {video.name}: 检测失败: {e}")

        print(f"本轮结束，{interval} 秒后继续...")
        time.sleep(interval)


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    interval, videos = load_config(config_path)
    print("抖音点赞监控已启动。")
    print(f"配置文件: {config_path}")
    print(f"监控数量: {len(videos)}")
    print(f"轮询间隔: {interval} 秒")
    monitor_loop(interval, videos)


if __name__ == "__main__":
    main()
