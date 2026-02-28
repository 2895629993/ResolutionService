"""
游戏分辨率自动切换程序 —— 本体主程序
- 提供三态状态机分辨率切换
- 提供网页配置修改入口
- 提供插件扩展加载与启动
"""

import datetime
import enum
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser

from config_manager import AppConfig, Resolution, load_config
from plugin_manager import PluginManager
from process_monitor import is_running
from resolution_controller import (
    get_current_resolution,
    get_current_resolution_info,
    set_default,
    set_enabled,
)
from web_config_server import WebConfigServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ROOT_DIR = os.path.dirname(BASE_DIR)
LOGS_DIR = os.path.join(BASE_DIR, "Logs")
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
EXTERNAL_PLUGINS_DIR = os.path.join(SERVICE_ROOT_DIR, "plugins")

logger = logging.getLogger("main")


def _create_log_dir() -> str:
    now = datetime.datetime.now()
    date_dir = now.strftime("%Y-%m-%d")
    time_dir = now.strftime("%H-%M-%S")
    log_folder = os.path.join(LOGS_DIR, date_dir, time_dir)
    os.makedirs(log_folder, exist_ok=True)
    return os.path.join(log_folder, "log.txt")


def _cleanup_old_logs(max_age_days: int = 2) -> None:
    if not os.path.isdir(LOGS_DIR):
        return

    today = datetime.date.today()
    for entry in os.listdir(LOGS_DIR):
        entry_path = os.path.join(LOGS_DIR, entry)
        if not os.path.isdir(entry_path):
            continue
        try:
            folder_date = datetime.datetime.strptime(entry, "%Y-%m-%d").date()
        except ValueError:
            continue

        age = (today - folder_date).days
        if age > max_age_days:
            try:
                shutil.rmtree(entry_path)
                print(f"[清理] 已删除过期日志目录: {entry_path} (已过 {age} 天)")
            except OSError as exc:
                print(f"[清理] 删除失败: {entry_path} - {exc}")


def _setup_logging(foreground: bool = False) -> None:
    log_file = _create_log_dir()
    handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    if foreground:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def _daemonize() -> None:
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable

    script = os.path.abspath(__file__)
    cmd = [pythonw, script, "--fg"]

    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000

    subprocess.Popen(
        cmd,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
        cwd=BASE_DIR,
    )
    print("程序已在后台启动")
    print(f"日志目录: {LOGS_DIR}")
    sys.exit(0)


def _open_browser_async(url: str, delay_seconds: float = 0.8) -> None:
    def _worker() -> None:
        time.sleep(delay_seconds)
        try:
            webbrowser.open(url, new=2)
            logger.info("已尝试打开浏览器: %s", url)
        except Exception as exc:
            logger.warning("自动打开浏览器失败: %s", exc)

    threading.Thread(target=_worker, name="BrowserAutoOpen", daemon=True).start()


class State(enum.Enum):
    ALPHA = "α - 等待游戏启动"
    BETA = "β - 游戏运行中"
    GAMMA = "γ - 游戏已关闭，等待重启或退出"


_stop_event = threading.Event()


def _register_shutdown_hooks(web_server: WebConfigServer | None, plugin_manager: PluginManager | None) -> None:
    def _shutdown(*_args):
        _stop_event.set()
        if plugin_manager:
            plugin_manager.stop_all()
        if web_server:
            web_server.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)


def run(config: AppConfig) -> None:
    state = State.ALPHA
    alpha_enter_time = time.monotonic()

    logger.info("程序启动，当前分辨率: %s", get_current_resolution())
    logger.info("监控启动器: %s | 游戏: %s", config.launcher_process, config.game_process)
    logger.info("进入状态: %s", state.value)

    while not _stop_event.is_set():
        try:
            if state is State.ALPHA:
                if time.monotonic() - alpha_enter_time >= 10:
                    if not is_running(config.launcher_process):
                        logger.info("启动器 [%s] 已关闭，程序退出", config.launcher_process)
                        break

                if is_running(config.game_process):
                    logger.info("检测到游戏进程 [%s] 启动", config.game_process)
                    r = config.enabled_resolution
                    set_enabled(r.width, r.height, r.refresh_rate)
                    state = State.BETA
                    logger.info("进入状态: %s", state.value)

                time.sleep(0.5)

            elif state is State.BETA:
                if not is_running(config.game_process):
                    logger.info("检测到游戏进程 [%s] 已关闭", config.game_process)
                    r = config.default_resolution
                    set_default(r.width, r.height, r.refresh_rate)
                    state = State.GAMMA
                    logger.info("进入状态: %s", state.value)

                time.sleep(2)

            elif state is State.GAMMA:
                launcher_alive = is_running(config.launcher_process)
                game_alive = is_running(config.game_process)

                if not launcher_alive:
                    logger.info("启动器 [%s] 已关闭，程序退出", config.launcher_process)
                    break

                if game_alive:
                    logger.info("检测到游戏进程 [%s] 重新启动", config.game_process)
                    r = config.enabled_resolution
                    set_enabled(r.width, r.height, r.refresh_rate)
                    state = State.BETA
                    logger.info("进入状态: %s", state.value)

                time.sleep(2)

        except Exception as exc:
            logger.error("状态机异常: %s", exc, exc_info=True)
            time.sleep(2)

    logger.info("程序已结束，当前分辨率: %s", get_current_resolution())


def _run_config_ui_loop() -> None:
    logger.info("已进入配置界面模式，按 Ctrl+C 退出")
    try:
        while not _stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，配置界面模式退出")


def main() -> None:
    args = set(sys.argv[1:])
    foreground = "--fg" in args
    daemon_mode = "--daemon" in args
    config_ui_mode = "--config-ui" in args
    with_web = "--with-web" in args or config_ui_mode
    open_browser = "--open-browser" in args or config_ui_mode

    if daemon_mode and config_ui_mode:
        print("参数冲突: --daemon 与 --config-ui 不能同时使用")
        sys.exit(2)

    if daemon_mode:
        _daemonize()

    _cleanup_old_logs(max_age_days=2)
    _setup_logging(foreground=foreground or config_ui_mode or not daemon_mode)

    config = load_config(CONFIG_PATH)

    if config.auto_capture_default_on_start and not config_ui_mode:
        try:
            w, h, rr = get_current_resolution_info()
            config.default_resolution = Resolution(w, h, rr)
            logger.info("已记录启动时默认分辨率: %s", config.default_resolution)
        except Exception as exc:
            logger.warning(
                "记录启动时默认分辨率失败，继续使用配置默认值: %s (%s)",
                config.default_resolution,
                exc,
            )

    logger.info("配置加载完成:")
    logger.info("  启动器进程: %s", config.launcher_process)
    logger.info("  游戏进程:   %s", config.game_process)
    logger.info("  默认分辨率: %s", config.default_resolution)
    logger.info("  启用分辨率: %s", config.enabled_resolution)

    app_state = {
        "config": config,
        "config_path": CONFIG_PATH,
        "base_dir": BASE_DIR,
        "plugins_dir": PLUGINS_DIR,
        "logger": logger,
        "web_modules": [],
    }

    plugin_manager: PluginManager | None = None
    if config.enable_plugins:
        try:
            plugin_manager = PluginManager(PLUGINS_DIR)
            plugin_manager.load_all()
            app_state["plugin_manager"] = plugin_manager
            app_state["web_modules_provider"] = lambda state: plugin_manager.list_web_modules(state)
            app_state["web_modules"] = plugin_manager.list_web_modules(app_state)
        except Exception as exc:
            logger.error("插件系统加载失败: %s", exc, exc_info=True)

    web_server: WebConfigServer | None = None
    should_start_web = with_web and (config.web_enabled or config_ui_mode)
    if should_start_web:
        if config_ui_mode and not config.web_enabled:
            logger.info("检测到配置界面模式，已忽略 Web.Enabled=false 并强制开启网页服务")
        try:
            web_server = WebConfigServer(config.web_host, config.web_port, app_state)
            web_server.start()
            if open_browser:
                url = f"http://{config.web_host}:{config.web_port}"
                _open_browser_async(url)
        except Exception as exc:
            logger.error("网页配置服务启动失败: %s", exc, exc_info=True)
    elif with_web and not config.web_enabled:
        logger.warning("Web.Enabled=false，网页配置服务未启动")

    if plugin_manager and not config_ui_mode:
        try:
            plugin_manager.start_all(app_state)
        except Exception as exc:
            logger.error("插件系统启动失败: %s", exc, exc_info=True)

    _register_shutdown_hooks(web_server, plugin_manager)

    try:
        if config_ui_mode:
            _run_config_ui_loop()
        else:
            run(config)
    finally:
        if plugin_manager:
            plugin_manager.stop_all()
        if web_server:
            web_server.stop()


if __name__ == "__main__":
    main()
