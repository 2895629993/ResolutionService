"""
进程监控模块
封装 psutil 进程检测逻辑，供主程序调用。
"""

import logging

import psutil

logger = logging.getLogger(__name__)


def is_running(process_name: str) -> bool:
    """
    检测指定进程是否正在运行。

    Parameters
    ----------
    process_name : str
        进程名称，例如 'B.exe'（不区分大小写）。

    Returns
    -------
    bool
        进程存在返回 True，否则返回 False。
    """
    target = process_name.lower()
    try:
        for proc in psutil.process_iter(attrs=["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == target:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as exc:
        logger.error("进程检测异常: %s", exc)
    return False
