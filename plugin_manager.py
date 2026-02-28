"""
扩展插件管理模块
从 plugins 目录加载插件，并按生命周期调用。

插件接口：
- 可选常量: PLUGIN_NAME = "名字"
- 可选常量: PLUGIN_ID = "module-id"
- 必须函数: start(context: dict) -> None
- 可选函数: stop() -> None
- 可选函数: get_web_module(context: dict) -> dict
  返回示例:
  {
      "id": "batch-edit",
      "title": "批量修改文件",
      "html": "<p>这里是插件自己的模块页面</p>",
  }
- 可选函数: handle_web_action(action: str, form: dict, context: dict) -> str
  由网页配置服务回调，用于处理插件自定义配置保存。
"""

import importlib.util
import logging
import os
import re
import threading
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class LoadedPlugin:
    name: str
    path: str
    module: ModuleType
    module_id: str
    started: bool = False


class PluginManager:
    def __init__(self, plugins_root: str) -> None:
        self.plugins_root = plugins_root
        self._loaded: List[LoadedPlugin] = []
        self._lock = threading.Lock()

    def discover_plugin_files(self) -> List[str]:
        """发现 plugins/*/plugin.py 文件。"""
        plugin_files: List[str] = []
        if not os.path.isdir(self.plugins_root):
            return plugin_files

        for entry in os.listdir(self.plugins_root):
            plugin_dir = os.path.join(self.plugins_root, entry)
            if not os.path.isdir(plugin_dir):
                continue
            plugin_file = os.path.join(plugin_dir, "plugin.py")
            if os.path.isfile(plugin_file):
                plugin_files.append(plugin_file)

        plugin_files.sort()
        return plugin_files

    def _sanitize_module_id(self, raw_id: str, fallback: str) -> str:
        text = (raw_id or "").strip().lower()
        if not text:
            text = fallback.strip().lower()
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text or (fallback.strip().lower() or "plugin")

    def _next_unique_id(self, preferred_id: str, used_ids: Set[str]) -> str:
        if preferred_id not in used_ids:
            return preferred_id
        idx = 2
        while f"{preferred_id}-{idx}" in used_ids:
            idx += 1
        return f"{preferred_id}-{idx}"

    def load_all(self) -> List[LoadedPlugin]:
        with self._lock:
            self._loaded = []
            used_ids: Set[str] = set()
            for plugin_file in self.discover_plugin_files():
                plugin = self._load_one(plugin_file, used_ids)
                if plugin:
                    self._loaded.append(plugin)
            return list(self._loaded)

    def _load_one(self, plugin_path: str, used_ids: Set[str]) -> Optional[LoadedPlugin]:
        plugin_dir = os.path.dirname(plugin_path)
        folder_name = os.path.basename(plugin_dir)
        safe_folder_name = re.sub(r"[^0-9a-zA-Z_]", "_", folder_name)
        module_name = f"plugin_{safe_folder_name or 'plugin'}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法创建模块加载器")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "start"):
                logger.warning("插件缺少 start(context) 函数，已跳过: %s", plugin_path)
                return None

            plugin_name = str(getattr(module, "PLUGIN_NAME", folder_name)).strip() or folder_name
            raw_id = str(getattr(module, "PLUGIN_ID", folder_name))
            base_module_id = self._sanitize_module_id(raw_id, folder_name)
            module_id = self._next_unique_id(base_module_id, used_ids)
            used_ids.add(module_id)

            logger.info("插件加载成功: %s (%s)", plugin_name, plugin_path)
            return LoadedPlugin(
                name=plugin_name,
                path=plugin_path,
                module=module,
                module_id=module_id,
            )

        except Exception as exc:
            logger.error("插件加载失败: %s (%s)", plugin_path, exc, exc_info=True)
            return None

    def start_all(self, context: Dict) -> None:
        with self._lock:
            for plugin in self._loaded:
                if plugin.started:
                    continue
                try:
                    plugin.module.start(context)
                    plugin.started = True
                    logger.info("插件已启动: %s", plugin.name)
                except Exception as exc:
                    logger.error("插件启动失败: %s (%s)", plugin.name, exc, exc_info=True)

    def list_web_modules(self, context: Dict) -> List[Dict[str, str]]:
        """收集插件提供的网页模块页面。"""
        modules: List[Dict[str, str]] = []
        used_ids: Set[str] = set()

        with self._lock:
            for plugin in self._loaded:
                get_module = getattr(plugin.module, "get_web_module", None)
                if not callable(get_module):
                    continue

                try:
                    payload = get_module(context)
                    if payload is None:
                        continue
                    if not isinstance(payload, dict):
                        raise TypeError("get_web_module 必须返回 dict")

                    raw_id = str(payload.get("id", plugin.module_id))
                    module_id = self._sanitize_module_id(raw_id, plugin.module_id)
                    module_id = self._next_unique_id(module_id, used_ids)
                    used_ids.add(module_id)

                    title = str(payload.get("title", plugin.name)).strip() or plugin.name
                    body_html = str(payload.get("html", ""))

                    modules.append(
                        {
                            "id": module_id,
                            "title": title,
                            "html": body_html,
                            "plugin_name": plugin.name,
                        }
                    )
                except Exception as exc:
                    logger.error("插件页面构建失败: %s (%s)", plugin.name, exc, exc_info=True)

        return modules

    def dispatch_web_action(self, plugin_id: str, action: str, form: Dict, context: Dict) -> str:
        """将网页配置动作分发到指定插件。"""
        raw_id = (plugin_id or "").strip()
        if not raw_id:
            raise ValueError("缺少 plugin_id")

        normalized_id = self._sanitize_module_id(raw_id, raw_id)

        with self._lock:
            target = None
            for plugin in self._loaded:
                if plugin.module_id == normalized_id:
                    target = plugin
                    break

            if target is None:
                raise ValueError(f"未找到插件: {raw_id}")

            handle_fn = getattr(target.module, "handle_web_action", None)
            if not callable(handle_fn):
                raise ValueError(f"插件 [{target.name}] 不支持网页配置操作")

            result = handle_fn(action, form, context)
            text = str(result).strip() if result is not None else ""
            return text or f"插件 [{target.name}] 操作已完成"

    def stop_all(self) -> None:
        with self._lock:
            for plugin in reversed(self._loaded):
                if not plugin.started:
                    continue
                try:
                    stop_fn = getattr(plugin.module, "stop", None)
                    if callable(stop_fn):
                        stop_fn()
                    logger.info("插件已停止: %s", plugin.name)
                except Exception as exc:
                    logger.error("插件停止失败: %s (%s)", plugin.name, exc, exc_info=True)
