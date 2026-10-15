"""后台 worker 入口：python -m app.worker。

M1 阶段只是空循环，保证容器能正常运行并响应停止信号；
取任务、解析、审核、备份、定时清理在后续里程碑加入。
"""
import logging
import signal
import threading

from app.config import get_settings, setup_logging

logger = logging.getLogger("app.worker")

IDLE_SECONDS = 5

_stop = threading.Event()


def _handle_signal(signum: int, _frame: object) -> None:
    logger.info("收到停止信号 %s，准备退出", signum)
    _stop.set()


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    settings = get_settings()
    logger.info("worker 启动，版本 %s，并发 %s", settings.app_version, settings.worker_concurrency)
    while not _stop.is_set():
        # 后续任务在这里从 jobs 表取任务
        _stop.wait(IDLE_SECONDS)
    logger.info("worker 已退出")


if __name__ == "__main__":
    main()
