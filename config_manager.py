"""
配置管理模块
负责解析/保存 config.ini，提供统一配置数据结构。
"""

import configparser
import logging
import os
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)


@dataclass
class Resolution:
    """分辨率参数"""

    width: int
    height: int
    refresh_rate: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height} @{self.refresh_rate}Hz"


@dataclass
class AppConfig:
    """应用全局配置"""

    launcher_process: str
    game_process: str
    default_resolution: Resolution
    enabled_resolution: Resolution
    web_enabled: bool
    web_host: str
    web_port: int
    auto_capture_default_on_start: bool
    enable_plugins: bool


_DEFAULT_CONFIG = AppConfig(
    launcher_process="A.exe",
    game_process="B.exe",
    default_resolution=Resolution(1920, 1080, 60),
    enabled_resolution=Resolution(1280, 720, 144),
    web_enabled=True,
    web_host="127.0.0.1",
    web_port=8765,
    auto_capture_default_on_start=True,
    enable_plugins=True,
)


def _clone_default() -> AppConfig:
    """返回默认配置的可变副本。"""
    return AppConfig(
        launcher_process=_DEFAULT_CONFIG.launcher_process,
        game_process=_DEFAULT_CONFIG.game_process,
        default_resolution=Resolution(
            _DEFAULT_CONFIG.default_resolution.width,
            _DEFAULT_CONFIG.default_resolution.height,
            _DEFAULT_CONFIG.default_resolution.refresh_rate,
        ),
        enabled_resolution=Resolution(
            _DEFAULT_CONFIG.enabled_resolution.width,
            _DEFAULT_CONFIG.enabled_resolution.height,
            _DEFAULT_CONFIG.enabled_resolution.refresh_rate,
        ),
        web_enabled=_DEFAULT_CONFIG.web_enabled,
        web_host=_DEFAULT_CONFIG.web_host,
        web_port=_DEFAULT_CONFIG.web_port,
        auto_capture_default_on_start=_DEFAULT_CONFIG.auto_capture_default_on_start,
        enable_plugins=_DEFAULT_CONFIG.enable_plugins,
    )


def _parse_resolution(res_str: str) -> Tuple[int, int]:
    """
    解析 '1920 * 1080' 或 '1920x1080' 格式的分辨率字符串。
    返回 (width, height)。
    """
    for sep in ("*", "x", "X"):
        if sep in res_str:
            parts = res_str.split(sep)
            if len(parts) == 2:
                return int(parts[0].strip()), int(parts[1].strip())
    raise ValueError(f"无法解析分辨率字符串: '{res_str}'")


def _parse_bool(value: str, fallback: bool) -> bool:
    if value is None:
        return fallback
    text = value.strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return fallback


def load_config(path: str = None) -> AppConfig:
    """加载并解析配置文件。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

    if not os.path.isfile(path):
        logger.warning("配置文件不存在: %s，使用默认配置", path)
        return _clone_default()

    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        logger.error("配置文件读取失败: %s，使用默认配置", exc)
        return _clone_default()

    cfg = _clone_default()

    try:
        cfg.launcher_process = parser.get(
            "Launcher",
            "ProcessName",
            fallback=cfg.launcher_process,
        ).strip()
        cfg.game_process = parser.get(
            "Game",
            "ProcessName",
            fallback=cfg.game_process,
        ).strip()

        def_res_str = parser.get("Default", "Resolution", fallback="1920*1080")
        def_rr = parser.getint(
            "Default",
            "RefreshRate",
            fallback=cfg.default_resolution.refresh_rate,
        )
        def_w, def_h = _parse_resolution(def_res_str)
        cfg.default_resolution = Resolution(def_w, def_h, def_rr)

        en_res_str = parser.get("Enabled", "Resolution", fallback="1280*720")
        en_rr = parser.getint(
            "Enabled",
            "RefreshRate",
            fallback=cfg.enabled_resolution.refresh_rate,
        )
        en_w, en_h = _parse_resolution(en_res_str)
        cfg.enabled_resolution = Resolution(en_w, en_h, en_rr)

        cfg.web_enabled = _parse_bool(
            parser.get("Web", "Enabled", fallback=str(cfg.web_enabled)),
            cfg.web_enabled,
        )
        cfg.web_host = parser.get("Web", "Host", fallback=cfg.web_host).strip()
        cfg.web_port = parser.getint("Web", "Port", fallback=cfg.web_port)

        cfg.auto_capture_default_on_start = _parse_bool(
            parser.get(
                "Runtime",
                "AutoCaptureDefaultOnStart",
                fallback=str(cfg.auto_capture_default_on_start),
            ),
            cfg.auto_capture_default_on_start,
        )
        cfg.enable_plugins = _parse_bool(
            parser.get("Runtime", "EnablePlugins", fallback=str(cfg.enable_plugins)),
            cfg.enable_plugins,
        )

        logger.info("配置加载成功: %s", cfg)
        return cfg

    except (ValueError, configparser.Error) as exc:
        logger.error("配置解析失败: %s，使用默认配置", exc)
        return _clone_default()


def save_config(config: AppConfig, path: str = None) -> None:
    """保存配置到 ini 文件。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

    parser = configparser.ConfigParser()

    parser["Launcher"] = {
        "ProcessName": config.launcher_process,
    }
    parser["Game"] = {
        "ProcessName": config.game_process,
    }
    parser["Default"] = {
        "Resolution": f"{config.default_resolution.width} * {config.default_resolution.height}",
        "RefreshRate": str(config.default_resolution.refresh_rate),
    }
    parser["Enabled"] = {
        "Resolution": f"{config.enabled_resolution.width} * {config.enabled_resolution.height}",
        "RefreshRate": str(config.enabled_resolution.refresh_rate),
    }
    parser["Web"] = {
        "Enabled": str(config.web_enabled).lower(),
        "Host": config.web_host,
        "Port": str(config.web_port),
    }
    parser["Runtime"] = {
        "AutoCaptureDefaultOnStart": str(config.auto_capture_default_on_start).lower(),
        "EnablePlugins": str(config.enable_plugins).lower(),
    }

    with open(path, "w", encoding="utf-8") as fp:
        parser.write(fp)

    logger.info("配置已保存: %s", path)
