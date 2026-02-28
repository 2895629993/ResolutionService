"""
分辨率控制模块
通过 Windows API (ChangeDisplaySettingsW) 切换系统分辨率和刷新率。
"""

import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)

DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
DISP_CHANGE_SUCCESSFUL = 0


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.wintypes.WORD),
        ("dmDriverVersion", ctypes.wintypes.WORD),
        ("dmSize", ctypes.wintypes.WORD),
        ("dmDriverExtra", ctypes.wintypes.WORD),
        ("dmFields", ctypes.wintypes.DWORD),
        ("_padding1", ctypes.c_byte * 16),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.wintypes.WORD),
        ("dmBitsPerPel", ctypes.wintypes.DWORD),
        ("dmPelsWidth", ctypes.wintypes.DWORD),
        ("dmPelsHeight", ctypes.wintypes.DWORD),
        ("dmDisplayFlags", ctypes.wintypes.DWORD),
        ("dmDisplayFrequency", ctypes.wintypes.DWORD),
    ]


def _get_current_settings() -> DEVMODEW:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
    return dm


def _apply_resolution(width: int, height: int, refresh_rate: int) -> bool:
    dm = _get_current_settings()
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmDisplayFrequency = refresh_rate
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY

    test_result = ctypes.windll.user32.ChangeDisplaySettingsW(ctypes.byref(dm), CDS_TEST)
    if test_result != DISP_CHANGE_SUCCESSFUL:
        logger.error(
            "分辨率不受支持: %dx%d @%dHz (错误码: %d)",
            width,
            height,
            refresh_rate,
            test_result,
        )
        return False

    result = ctypes.windll.user32.ChangeDisplaySettingsW(
        ctypes.byref(dm),
        CDS_UPDATEREGISTRY,
    )
    if result == DISP_CHANGE_SUCCESSFUL:
        logger.info("分辨率已切换: %dx%d @%dHz", width, height, refresh_rate)
        return True

    logger.error(
        "分辨率切换失败: %dx%d @%dHz (错误码: %d)",
        width,
        height,
        refresh_rate,
        result,
    )
    return False


def set_enabled(width: int, height: int, refresh_rate: int) -> bool:
    logger.info("正在切换到启用分辨率: %dx%d @%dHz", width, height, refresh_rate)
    return _apply_resolution(width, height, refresh_rate)


def set_default(width: int, height: int, refresh_rate: int) -> bool:
    logger.info("正在切换到默认分辨率: %dx%d @%dHz", width, height, refresh_rate)
    return _apply_resolution(width, height, refresh_rate)


def get_current_resolution() -> str:
    dm = _get_current_settings()
    return f"{dm.dmPelsWidth}x{dm.dmPelsHeight} @{dm.dmDisplayFrequency}Hz"


def get_current_resolution_info() -> tuple:
    dm = _get_current_settings()
    return dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency
