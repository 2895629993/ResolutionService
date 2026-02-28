"""
网页配置服务模块
提供一个轻量 Web UI，用于修改 config.ini，
并支持插件模块页面在同一页面内切换显示。
"""

import html
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from config_manager import AppConfig, Resolution, save_config

logger = logging.getLogger(__name__)


class _ConfigHandler(BaseHTTPRequestHandler):
    app_state = None

    def _send_html(self, text: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _collect_web_modules(self):
        modules = []
        provider = self.app_state.get("web_modules_provider")

        if callable(provider):
            try:
                modules = provider(self.app_state) or []
            except Exception as exc:
                logger.error("加载插件模块页面失败: %s", exc, exc_info=True)
                modules = []
        else:
            modules = self.app_state.get("web_modules") or []

        safe_modules = []
        for item in modules:
            if not isinstance(item, dict):
                continue

            module_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            body_html = str(item.get("html", ""))
            plugin_name = str(item.get("plugin_name", "")).strip() or "插件"

            if not module_id or not title:
                continue

            safe_modules.append(
                {
                    "id": module_id,
                    "title": title,
                    "html": body_html,
                    "plugin_name": plugin_name,
                }
            )

        return safe_modules

    def _render_form(self, message: str = "") -> str:
        config: AppConfig = self.app_state["config"]
        escaped_msg = html.escape(message)
        modules = self._collect_web_modules()

        options_html = ["<option value=\"core-config\">基础配置</option>"]
        plugin_panels = []

        for module in modules:
            module_id = html.escape(module["id"])
            module_title = html.escape(module["title"])
            plugin_name = html.escape(module["plugin_name"])

            options_html.append(f"<option value=\"{module_id}\">{module_title}</option>")
            plugin_panels.append(
                f"""
                <section class="panel" data-module="{module_id}" hidden>
                  <h2>{module_title}</h2>
                  <div class="meta">来源：{plugin_name}</div>
                  <div class="plugin-body">{module['html']}</div>
                </section>
                """
            )

        return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>GameResolutionService 配置页</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #f6f8fc; color: #1f2937; }}
    .layout {{ display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }}
    .sidebar {{ border-right: 1px solid #dbe3ef; background: #f8fbff; padding: 20px; }}
    .sidebar h1 {{ margin: 0 0 10px; font-size: 18px; }}
    .sidebar p {{ margin: 0 0 12px; color: #64748b; font-size: 13px; line-height: 1.5; }}
    .sidebar label {{ display: block; margin-bottom: 8px; color: #64748b; font-size: 13px; }}
    .sidebar select {{ width: 100%; padding: 8px; border: 1px solid #dbe3ef; border-radius: 6px; background: #fff; }}
    .sidebar .file {{ margin-top: 12px; color: #64748b; font-size: 12px; line-height: 1.5; }}
    .content {{ padding: 24px; }}
    .panel {{ max-width: 900px; background: #fff; border: 1px solid #dbe3ef; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(30, 41, 59, 0.05); }}
    .msg {{ color: #0a7a2f; margin: 12px 0; min-height: 20px; }}
    .meta {{ color: #64748b; font-size: 12px; margin-bottom: 12px; }}
    .row {{ margin: 8px 0; }}
    label.input {{ display: inline-block; width: 220px; }}
    input[type=text], input[type=number] {{ width: 260px; padding: 6px; border: 1px solid #dbe3ef; border-radius: 6px; }}
    fieldset {{ margin-bottom: 14px; border: 1px solid #dbe3ef; border-radius: 8px; }}
    legend {{ color: #64748b; }}
    button {{ padding: 8px 16px; border: none; border-radius: 8px; background: #2563eb; color: #fff; cursor: pointer; }}
    code {{ background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }}
    .plugin-body {{ line-height: 1.6; }}
    .plugin-body table {{ border-collapse: collapse; width: 100%; }}
    .plugin-body th, .plugin-body td {{ border: 1px solid #dbe3ef; padding: 6px 8px; text-align: left; }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h1>配置中心</h1>
      <p>左侧下拉菜单可在“基础配置”和扩展模块之间切换，不会打开新网页。</p>
      <label for="module-select">模块选择</label>
      <select id="module-select">
        {"".join(options_html)}
      </select>
      <div class="file">配置文件：<br /><code>{html.escape(self.app_state['config_path'])}</code></div>
    </aside>

    <main class="content">
      <section class="panel" data-module="core-config">
        <h2>基础配置</h2>
        <div class="msg">{escaped_msg}</div>
        <form method="post" action="/save">
          <fieldset>
            <legend>进程设置</legend>
            <div class="row"><label class="input">启动器进程名</label><input type="text" name="launcher_process" value="{html.escape(config.launcher_process)}" /></div>
            <div class="row"><label class="input">游戏进程名</label><input type="text" name="game_process" value="{html.escape(config.game_process)}" /></div>
          </fieldset>

          <fieldset>
            <legend>默认分辨率（退回）</legend>
            <div class="row"><label class="input">宽度</label><input type="number" name="default_width" value="{config.default_resolution.width}" /></div>
            <div class="row"><label class="input">高度</label><input type="number" name="default_height" value="{config.default_resolution.height}" /></div>
            <div class="row"><label class="input">刷新率</label><input type="number" name="default_refresh" value="{config.default_resolution.refresh_rate}" /></div>
          </fieldset>

          <fieldset>
            <legend>启用分辨率（游戏中）</legend>
            <div class="row"><label class="input">宽度</label><input type="number" name="enabled_width" value="{config.enabled_resolution.width}" /></div>
            <div class="row"><label class="input">高度</label><input type="number" name="enabled_height" value="{config.enabled_resolution.height}" /></div>
            <div class="row"><label class="input">刷新率</label><input type="number" name="enabled_refresh" value="{config.enabled_resolution.refresh_rate}" /></div>
          </fieldset>

          <fieldset>
            <legend>运行设置</legend>
            <div class="row"><label class="input">启动时记录当前分辨率</label><input type="checkbox" name="auto_capture_default_on_start" {'checked' if config.auto_capture_default_on_start else ''} /></div>
            <div class="row"><label class="input">启用插件系统</label><input type="checkbox" name="enable_plugins" {'checked' if config.enable_plugins else ''} /></div>
          </fieldset>

          <button type="submit">保存配置</button>
        </form>
      </section>

      {"".join(plugin_panels)}
    </main>
  </div>

  <script>
    (function () {{
      const select = document.getElementById("module-select");
      const panels = document.querySelectorAll("[data-module]");

      function switchModule(moduleId) {{
        panels.forEach(function (panel) {{
          panel.hidden = panel.getAttribute("data-module") !== moduleId;
        }});
      }}

      switchModule(select.value || "core-config");
      select.addEventListener("change", function () {{
        switchModule(select.value || "core-config");
      }});
    }})();
  </script>
</body>
</html>
"""

    def _parse_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body)

    def _save_core_config(self, form) -> str:
        cfg: AppConfig = self.app_state["config"]

        def _get(name: str, default: str = "") -> str:
            return form.get(name, [default])[0].strip()

        cfg.launcher_process = _get("launcher_process", cfg.launcher_process)
        cfg.game_process = _get("game_process", cfg.game_process)

        cfg.default_resolution = Resolution(
            int(_get("default_width", str(cfg.default_resolution.width))),
            int(_get("default_height", str(cfg.default_resolution.height))),
            int(_get("default_refresh", str(cfg.default_resolution.refresh_rate))),
        )

        cfg.enabled_resolution = Resolution(
            int(_get("enabled_width", str(cfg.enabled_resolution.width))),
            int(_get("enabled_height", str(cfg.enabled_resolution.height))),
            int(_get("enabled_refresh", str(cfg.enabled_resolution.refresh_rate))),
        )

        cfg.auto_capture_default_on_start = "auto_capture_default_on_start" in form
        cfg.enable_plugins = "enable_plugins" in form

        save_config(cfg, self.app_state["config_path"])
        return "保存成功：配置已写入 config.ini"

    def _dispatch_plugin_action(self, form) -> str:
        plugin_manager = self.app_state.get("plugin_manager")
        if plugin_manager is None:
            raise ValueError("插件系统未启用，无法执行插件配置操作")

        plugin_id = form.get("plugin_id", [""])[0].strip()
        action = form.get("action", [""])[0].strip()

        if not plugin_id:
            raise ValueError("缺少 plugin_id")
        if not action:
            raise ValueError("缺少 action")

        return plugin_manager.dispatch_web_action(plugin_id, action, form, self.app_state)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html(self._render_form())
            return
        self._send_html("<h1>404</h1>", status=HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path not in ("/save", "/plugin-action"):
            self._send_html("<h1>404</h1>", status=HTTPStatus.NOT_FOUND)
            return

        try:
            form = self._parse_form()

            if self.path == "/save":
                message = self._save_core_config(form)
            else:
                message = self._dispatch_plugin_action(form)

            self._send_html(self._render_form(message))

        except Exception as exc:
            logger.error("网页提交处理失败: %s", exc, exc_info=True)
            self._send_html(self._render_form(f"操作失败: {exc}"), status=HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt, *args):
        logger.info("[web] %s - %s", self.client_address[0], fmt % args)


class WebConfigServer:
    def __init__(self, host: str, port: int, app_state: dict) -> None:
        self.host = host
        self.port = port
        self.app_state = app_state
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        _ConfigHandler.app_state = self.app_state
        self._server = ThreadingHTTPServer((self.host, self.port), _ConfigHandler)

        def _serve() -> None:
            logger.info("网页配置服务启动: http://%s:%s", self.host, self.port)
            self._server.serve_forever(poll_interval=0.5)

        self._thread = threading.Thread(target=_serve, name="WebConfigServer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            logger.info("网页配置服务已停止")
        self._server = None
        self._thread = None
