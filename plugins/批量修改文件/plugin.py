"""
批量修改文件插件（示例）

插件接口：
- PLUGIN_NAME
- PLUGIN_ID
- start(context)
- stop()
- get_web_module(context)
- handle_web_action(action, form, context)

说明：
1) 读取本插件目录下 `tasks.json`
2) 按规则对目标文本做批量替换
3) 支持两种规则模式：
   - anchor: line_range + from/to 锚点替换
   - regex: 正则表达式匹配替换（支持 flags/count）
4) 支持插件配置栏，用户可自定义插件行为并保存到 `plugin_config.json`
"""

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

PLUGIN_NAME = "批量修改文件"
PLUGIN_ID = "batch-edit"
PLUGIN_FOLDER_NAME = "批量修改文件"
SETTINGS_FILE_NAME = "plugin_config.json"
TASKS_FILE_NAME = "tasks.json"
SUPPORTED_TEMPLATE_VARS = (
    "enabled_width",
    "enabled_height",
    "enabled_refresh",
)

_TEMPLATE_TOKEN_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

_logger: logging.Logger | None = None


@dataclass
class EditRule:
    file: str
    mode: str = "anchor"
    line_range: Tuple[int, int] | None = None
    from_text: str = ""
    to_text: str = ""
    new_text: str = ""
    inclusive: bool = True
    pattern: str = ""
    replacement: str = ""
    regex_flags: str = ""
    regex_count: int = 0


@dataclass
class PluginSettings:
    enabled: bool = True
    dry_run: bool = False
    stop_on_error: bool = False
    backup_before_write: bool = True


def _runtime_logger() -> logging.Logger:
    return _logger or logging.getLogger(__name__)


def _parse_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "y", "on"):
            return True
        if text in ("0", "false", "no", "n", "off"):
            return False
    return default


def _settings_path(plugin_dir: str) -> str:
    return os.path.join(plugin_dir, SETTINGS_FILE_NAME)


def _tasks_path(plugin_dir: str) -> str:
    return os.path.join(plugin_dir, TASKS_FILE_NAME)


def _default_tasks_data() -> dict:
    return {"edits": []}


def _normalize_tasks_data(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("tasks.json 顶层必须是对象")

    edits = data.get("edits", [])
    if edits is None:
        edits = []
    if not isinstance(edits, list):
        raise ValueError("tasks.json 的 edits 字段必须是数组")

    normalized = dict(data)
    normalized["edits"] = edits
    return normalized


def _normalize_newline(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _preview(text: str, limit: int = 72) -> str:
    s = text.replace("\n", "\\n")
    return s if len(s) <= limit else f"{s[:limit]}..."


def _parse_json_text(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列，{exc.msg}"
        ) from exc

    if not isinstance(value, dict):
        raise ValueError("tasks.json 顶层必须是对象")

    return value


def _parse_regex_flags(flag_text: str) -> int:
    flags = 0
    mapping = {
        "i": re.IGNORECASE,
        "m": re.MULTILINE,
        "s": re.DOTALL,
        "x": re.VERBOSE,
    }

    for char in (flag_text or "").lower():
        if char in (" ", "\t", ",", "|"):
            continue
        if char not in mapping:
            raise ValueError(f"不支持的 regex_flags 标记: {char}")
        flags |= mapping[char]

    return flags


def _resolve_plugin_dir(context: dict) -> str:
    plugins_dir = context.get("plugins_dir") if isinstance(context, dict) else None
    if plugins_dir:
        return os.path.join(plugins_dir, PLUGIN_FOLDER_NAME)
    return os.path.dirname(os.path.abspath(__file__))


def _build_template_vars(context: dict | None) -> Dict[str, str]:
    vars_map: Dict[str, str] = {}
    if not isinstance(context, dict):
        return vars_map

    config = context.get("config")
    enabled = getattr(config, "enabled_resolution", None)
    if enabled is None:
        return vars_map

    width = getattr(enabled, "width", None)
    height = getattr(enabled, "height", None)
    refresh = getattr(enabled, "refresh_rate", None)

    if width is not None:
        vars_map["enabled_width"] = str(width)
    if height is not None:
        vars_map["enabled_height"] = str(height)
    if refresh is not None:
        vars_map["enabled_refresh"] = str(refresh)

    return vars_map


def _apply_template_vars(text: str, template_vars: Dict[str, str]) -> str:
    if text is None:
        return ""

    raw = str(text)
    if "{{" not in raw or "}}" not in raw:
        return raw

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in template_vars:
            return match.group(0)
        return template_vars[key]

    return _TEMPLATE_TOKEN_PATTERN.sub(_replace, raw)


def _expand_rule_templates(rule: EditRule, template_vars: Dict[str, str]) -> EditRule:
    if not template_vars:
        return rule

    return EditRule(
        file=_apply_template_vars(rule.file, template_vars),
        mode=rule.mode,
        line_range=rule.line_range,
        from_text=_apply_template_vars(rule.from_text, template_vars),
        to_text=_apply_template_vars(rule.to_text, template_vars),
        new_text=_apply_template_vars(rule.new_text, template_vars),
        inclusive=rule.inclusive,
        pattern=_apply_template_vars(rule.pattern, template_vars),
        replacement=_apply_template_vars(rule.replacement, template_vars),
        regex_flags=rule.regex_flags,
        regex_count=rule.regex_count,
    )


def _load_settings(plugin_dir: str) -> PluginSettings:
    path = _settings_path(plugin_dir)
    if not os.path.isfile(path):
        return PluginSettings()

    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return PluginSettings()
        return PluginSettings(
            enabled=_parse_bool(data.get("enabled"), True),
            dry_run=_parse_bool(data.get("dry_run"), False),
            stop_on_error=_parse_bool(data.get("stop_on_error"), False),
            backup_before_write=_parse_bool(data.get("backup_before_write"), True),
        )
    except Exception as exc:
        _runtime_logger().warning("[%s] 读取插件配置失败，使用默认配置: %s", PLUGIN_NAME, exc)
        return PluginSettings()


def _save_settings(plugin_dir: str, settings: PluginSettings) -> str:
    os.makedirs(plugin_dir, exist_ok=True)
    path = _settings_path(plugin_dir)
    payload = {
        "enabled": settings.enabled,
        "dry_run": settings.dry_run,
        "stop_on_error": settings.stop_on_error,
        "backup_before_write": settings.backup_before_write,
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    return path


def _resolve_line_range(line_range: Tuple[int, int] | None, total_lines: int) -> Tuple[int, int]:
    if line_range is None:
        return 1, total_lines

    start_line, end_line = line_range
    if start_line < 1 or end_line < start_line or end_line > total_lines:
        raise ValueError(f"line_range 越界: {line_range}, 文件总行数={total_lines}")

    return start_line, end_line


def _replace_anchor(segment: str, rule: EditRule) -> str:
    from_index = segment.find(rule.from_text)
    if from_index < 0:
        raise ValueError(f"未找到 from 文本: {rule.from_text}")

    search_start = from_index + len(rule.from_text)
    to_index = segment.find(rule.to_text, search_start)
    if to_index < 0:
        raise ValueError(f"未找到 to 文本: {rule.to_text}")

    if rule.inclusive:
        left = segment[:from_index]
        right = segment[to_index + len(rule.to_text) :]
    else:
        left = segment[: from_index + len(rule.from_text)]
        right = segment[to_index:]

    return left + rule.new_text + right


def _replace_regex(segment: str, rule: EditRule) -> str:
    if not rule.pattern:
        raise ValueError("regex 模式必须提供 pattern")

    flags = _parse_regex_flags(rule.regex_flags)
    count = rule.regex_count if rule.regex_count > 0 else 0

    replaced_segment, replaced_count = re.subn(
        rule.pattern,
        rule.replacement,
        segment,
        count=count,
        flags=flags,
    )
    if replaced_count <= 0:
        raise ValueError(f"正则未匹配到任何内容: /{rule.pattern}/")

    return replaced_segment


def _apply_rule(base_dir: str, rule: EditRule, *, dry_run: bool, backup_before_write: bool) -> str:
    target_path = os.path.normpath(os.path.join(base_dir, rule.file))
    if not os.path.isfile(target_path):
        raise FileNotFoundError(f"文件不存在: {target_path}")

    with open(target_path, "r", encoding="utf-8") as fp:
        raw_text = fp.read()

    normalized = _normalize_newline(raw_text)
    lines = normalized.split("\n")
    start_line, end_line = _resolve_line_range(rule.line_range, len(lines))

    segment_lines = lines[start_line - 1 : end_line]
    segment = "\n".join(segment_lines)

    if rule.mode == "regex":
        replaced_segment = _replace_regex(segment, rule)
    else:
        replaced_segment = _replace_anchor(segment, rule)

    new_segment_lines = replaced_segment.split("\n")
    new_lines = lines[: start_line - 1] + new_segment_lines + lines[end_line:]
    out_text = "\n".join(new_lines)

    if dry_run:
        return f"{target_path} [dry-run]"

    if backup_before_write:
        backup_path = target_path + ".bak"
        with open(backup_path, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(raw_text)

    with open(target_path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(out_text)

    return target_path


def _parse_line_range(raw) -> Tuple[int, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError("line_range 必须是长度为 2 的数组")
    return int(raw[0]), int(raw[1])


def _parse_rules_from_data(data: dict) -> List[EditRule]:
    normalized = _normalize_tasks_data(data)
    edits = normalized["edits"]
    rules: List[EditRule] = []

    for index, item in enumerate(edits, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条规则必须是对象")

        file_path = str(item.get("file", "")).strip()
        if not file_path:
            raise ValueError(f"第 {index} 条规则缺少 file")

        mode = str(item.get("mode", "anchor")).strip().lower() or "anchor"
        if _parse_bool(item.get("use_regex", False), False):
            mode = "regex"
        if mode in ("re", "regexp"):
            mode = "regex"
        if mode not in ("anchor", "regex"):
            raise ValueError(f"第 {index} 条规则 mode 无效: {mode}")

        line_range = _parse_line_range(item.get("line_range"))

        if mode == "regex":
            pattern = str(item.get("pattern", ""))
            if not pattern:
                raise ValueError(f"第 {index} 条 regex 规则缺少 pattern")

            regex_flags_text = str(item.get("regex_flags", ""))
            regex_flags = _parse_regex_flags(regex_flags_text)

            try:
                re.compile(pattern, flags=regex_flags)
            except re.error as exc:
                raise ValueError(f"第 {index} 条 regex 规则无效: {exc}") from exc

            regex_count = int(item.get("regex_count", 0))
            if regex_count < 0:
                raise ValueError(f"第 {index} 条规则 regex_count 不能小于 0")

            rules.append(
                EditRule(
                    file=file_path,
                    mode="regex",
                    line_range=line_range,
                    pattern=pattern,
                    replacement=str(item.get("replacement", item.get("new_text", ""))),
                    regex_flags=regex_flags_text,
                    regex_count=regex_count,
                )
            )
            continue

        if "from" not in item or "to" not in item or "new_text" not in item:
            raise ValueError(f"第 {index} 条 anchor 规则缺少 from/to/new_text")

        rules.append(
            EditRule(
                file=file_path,
                mode="anchor",
                line_range=line_range,
                from_text=str(item["from"]),
                to_text=str(item["to"]),
                new_text=str(item["new_text"]),
                inclusive=_parse_bool(item.get("inclusive", True), True),
            )
        )

    return rules


def _load_tasks_data(plugin_dir: str) -> dict:
    path = _tasks_path(plugin_dir)
    if not os.path.isfile(path):
        return _default_tasks_data()

    with open(path, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    return _normalize_tasks_data(data)


def _save_tasks_data(plugin_dir: str, data: dict) -> str:
    normalized = _normalize_tasks_data(data)
    os.makedirs(plugin_dir, exist_ok=True)

    path = _tasks_path(plugin_dir)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(normalized, fp, ensure_ascii=False, indent=4)
        fp.write("\n")

    return path


def _read_tasks_text(plugin_dir: str) -> str:
    path = _tasks_path(plugin_dir)
    if not os.path.isfile(path):
        return json.dumps(_default_tasks_data(), ensure_ascii=False, indent=4) + "\n"

    with open(path, "r", encoding="utf-8") as fp:
        return fp.read()


def _empty_visual_edit() -> dict:
    return {
        "mode": "anchor",
        "line_start": "",
        "line_end": "",
        "from": "",
        "to": "",
        "action": "",
        "inclusive": True,
        "pattern": "",
        "regex_flags": "",
        "regex_count": "",
    }


def _tasks_for_visual_editor(plugin_dir: str) -> List[dict]:
    data = _load_tasks_data(plugin_dir)
    edits = data.get("edits", [])

    grouped: Dict[str, List[dict]] = {}
    ordered_files: List[str] = []

    for raw in edits:
        if not isinstance(raw, dict):
            continue

        file_path = str(raw.get("file", "")).strip()
        if file_path not in grouped:
            grouped[file_path] = []
            ordered_files.append(file_path)

        mode = str(raw.get("mode", "anchor")).strip().lower() or "anchor"
        if _parse_bool(raw.get("use_regex", False), False):
            mode = "regex"
        if mode in ("re", "regexp"):
            mode = "regex"
        if mode not in ("anchor", "regex"):
            mode = "anchor"

        line_start = ""
        line_end = ""
        line_range = raw.get("line_range")
        if isinstance(line_range, (list, tuple)) and len(line_range) == 2:
            line_start = str(line_range[0])
            line_end = str(line_range[1])

        item = _empty_visual_edit()
        item["mode"] = mode
        item["line_start"] = line_start
        item["line_end"] = line_end

        if mode == "regex":
            item["pattern"] = str(raw.get("pattern", ""))
            item["regex_flags"] = str(raw.get("regex_flags", ""))
            count_val = raw.get("regex_count", 0)
            item["regex_count"] = "" if str(count_val).strip() in ("", "0") else str(count_val)
            item["action"] = str(raw.get("replacement", raw.get("new_text", "")))
        else:
            item["from"] = str(raw.get("from", ""))
            item["to"] = str(raw.get("to", ""))
            item["inclusive"] = _parse_bool(raw.get("inclusive", True), True)
            item["action"] = str(raw.get("new_text", ""))

        grouped[file_path].append(item)

    tasks: List[dict] = []
    for file_path in ordered_files:
        task_edits = grouped.get(file_path, [])
        if not task_edits:
            task_edits = [_empty_visual_edit()]
        tasks.append({"file": file_path, "edits": task_edits})

    if not tasks:
        tasks.append({"file": "", "edits": [_empty_visual_edit()]})

    return tasks


def _json_for_script(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _parse_visual_tasks_payload(raw_text: str) -> Tuple[int, List[dict]]:
    data = _parse_json_text(raw_text)
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("任务数据缺少 tasks 数组")

    flat_edits: List[dict] = []

    for task_index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"任务 {task_index} 必须是对象")

        file_path = str(task.get("file", "")).strip()
        if not file_path:
            raise ValueError(f"任务 {task_index} 缺少文件地址 file")

        edits = task.get("edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError(f"任务 {task_index} 至少包含一条修改文本")

        for edit_index, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                raise ValueError(f"任务 {task_index} 的修改文本 {edit_index} 必须是对象")

            mode = str(edit.get("mode", "anchor")).strip().lower() or "anchor"
            if mode in ("re", "regexp"):
                mode = "regex"
            if mode not in ("anchor", "regex"):
                raise ValueError(f"任务 {task_index} 的修改文本 {edit_index} mode 无效: {mode}")

            line_start_text = str(edit.get("line_start", "")).strip()
            line_end_text = str(edit.get("line_end", "")).strip()
            line_range = None
            if line_start_text or line_end_text:
                if not line_start_text or not line_end_text:
                    raise ValueError(
                        f"任务 {task_index} 的修改文本 {edit_index} 行范围必须同时填写起始和结束"
                    )
                line_range = [int(line_start_text), int(line_end_text)]

            action_text = str(edit.get("action", ""))

            if mode == "regex":
                pattern = str(edit.get("pattern", "")).strip()
                if not pattern:
                    raise ValueError(f"任务 {task_index} 的修改文本 {edit_index} 缺少 pattern")

                regex_flags = str(edit.get("regex_flags", "")).strip()
                parsed_flags = _parse_regex_flags(regex_flags)
                try:
                    re.compile(pattern, flags=parsed_flags)
                except re.error as exc:
                    raise ValueError(
                        f"任务 {task_index} 的修改文本 {edit_index} 正则无效: {exc}"
                    ) from exc

                regex_count_text = str(edit.get("regex_count", "")).strip()
                regex_count = int(regex_count_text) if regex_count_text else 0
                if regex_count < 0:
                    raise ValueError(
                        f"任务 {task_index} 的修改文本 {edit_index} regex_count 不能小于 0"
                    )

                payload = {
                    "file": file_path,
                    "mode": "regex",
                    "pattern": pattern,
                    "replacement": action_text,
                    "regex_flags": regex_flags,
                    "regex_count": regex_count,
                }
            else:
                from_text = str(edit.get("from", ""))
                to_text = str(edit.get("to", ""))
                if from_text == "" or to_text == "":
                    raise ValueError(
                        f"任务 {task_index} 的修改文本 {edit_index} 缺少 from 或 to"
                    )

                payload = {
                    "file": file_path,
                    "mode": "anchor",
                    "from": from_text,
                    "to": to_text,
                    "new_text": action_text,
                    "inclusive": _parse_bool(edit.get("inclusive", True), True),
                }

            if line_range is not None:
                payload["line_range"] = line_range

            flat_edits.append(payload)

    if not flat_edits:
        raise ValueError("至少需要一条修改文本")

    return len(tasks), flat_edits


def _load_rules(plugin_dir: str) -> List[EditRule]:
    data = _load_tasks_data(plugin_dir)
    return _parse_rules_from_data(data)


def _execute_rules(
    base_dir: str,
    plugin_dir: str,
    settings: PluginSettings,
    *,
    force_run: bool = False,
    context: dict | None = None,
) -> Tuple[int, int, int]:
    runtime_logger = _runtime_logger()
    rules = _load_rules(plugin_dir)
    template_vars = _build_template_vars(context)

    if not rules:
        runtime_logger.info("[%s] 未发现 tasks.json 或没有编辑任务", PLUGIN_NAME)
        return 0, 0, 0

    if not force_run and not settings.enabled:
        runtime_logger.info("[%s] 插件已禁用，跳过执行", PLUGIN_NAME)
        return len(rules), 0, 0

    runtime_logger.info(
        "[%s] 开始执行批量修改，任务数: %d，dry_run=%s，stop_on_error=%s，backup_before_write=%s",
        PLUGIN_NAME,
        len(rules),
        settings.dry_run,
        settings.stop_on_error,
        settings.backup_before_write,
    )

    success_count = 0
    failed_count = 0

    for idx, rule in enumerate(rules, start=1):
        try:
            effective_rule = _expand_rule_templates(rule, template_vars)
            changed = _apply_rule(
                base_dir,
                effective_rule,
                dry_run=settings.dry_run,
                backup_before_write=settings.backup_before_write,
            )
            success_count += 1
            runtime_logger.info("[%s] 任务 #%d 成功: %s", PLUGIN_NAME, idx, changed)
        except Exception as exc:
            failed_count += 1
            runtime_logger.error("[%s] 任务 #%d 失败: %s", PLUGIN_NAME, idx, exc, exc_info=True)
            if settings.stop_on_error:
                runtime_logger.warning("[%s] stop_on_error=true，已停止后续任务", PLUGIN_NAME)
                break

    return len(rules), success_count, failed_count


def _render_rules_table(plugin_dir: str) -> str:
    try:
        rules = _load_rules(plugin_dir)
    except Exception as exc:
        return f"<p style='color:#b91c1c;'>任务解析失败：{html.escape(str(exc))}</p>"

    if not rules:
        return "<p>未检测到任务配置（tasks.json）或任务为空。</p>"

    rows = []
    for idx, rule in enumerate(rules, start=1):
        if rule.line_range:
            range_text = f"{rule.line_range[0]} - {rule.line_range[1]}"
        else:
            range_text = "全部"

        if rule.mode == "regex":
            match_desc = f"/{_preview(rule.pattern)}/"
            replace_desc = _preview(rule.replacement)
        else:
            match_desc = f"from={_preview(rule.from_text)}<br />to={_preview(rule.to_text)}"
            replace_desc = _preview(rule.new_text)

        rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{html.escape(rule.file)}</td>"
            f"<td>{html.escape(rule.mode)}</td>"
            f"<td>{html.escape(range_text)}</td>"
            f"<td>{match_desc}</td>"
            f"<td>{html.escape(replace_desc)}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>#</th><th>目标文件</th><th>模式</th><th>行范围</th><th>匹配规则</th><th>替换为</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _build_settings_panel(plugin_dir: str, settings: PluginSettings) -> str:
    checked_enabled = "checked" if settings.enabled else ""
    checked_dry_run = "checked" if settings.dry_run else ""
    checked_stop = "checked" if settings.stop_on_error else ""
    checked_backup = "checked" if settings.backup_before_write else ""

    return (
        "<h3>插件配置</h3>"
        "<p style='margin:8px 0;color:#64748b;'>可在此自定义插件行为，保存后立即生效。</p>"
        f"<p style='margin:8px 0;color:#64748b;'>配置文件：<code>{html.escape(_settings_path(plugin_dir))}</code></p>"
        "<form method='post' action='/plugin-action' style='margin-bottom:10px;'>"
        f"<input type='hidden' name='plugin_id' value='{html.escape(PLUGIN_ID)}' />"
        "<input type='hidden' name='action' value='save-settings' />"
        f"<div class='row'><label><input type='checkbox' name='enabled' {checked_enabled} /> 启用插件（服务启动时自动执行）</label></div>"
        f"<div class='row'><label><input type='checkbox' name='dry_run' {checked_dry_run} /> 仅演练（dry-run，不写文件）</label></div>"
        f"<div class='row'><label><input type='checkbox' name='stop_on_error' {checked_stop} /> 遇错即停（stop_on_error）</label></div>"
        f"<div class='row'><label><input type='checkbox' name='backup_before_write' {checked_backup} /> 写入前备份（生成 .bak）</label></div>"
        "<button type='submit'>保存插件配置(不是任务配置)</button>"
        "</form>"
        "<form method='post' action='/plugin-action'>"
        f"<input type='hidden' name='plugin_id' value='{html.escape(PLUGIN_ID)}' />"
        "<input type='hidden' name='action' value='run-now' />"
        "<button type='submit'>立即执行一次任务</button>"
        "</form>"
    )


def _build_tasks_editor_panel(plugin_dir: str) -> str:
    tasks_path = _tasks_path(plugin_dir)

    load_error_html = ""
    try:
        initial_tasks = _tasks_for_visual_editor(plugin_dir)
    except Exception as exc:
        initial_tasks = [{"file": "", "edits": [_empty_visual_edit()]}]
        load_error_html = (
            "<p style='margin:8px 0;color:#b91c1c;'>"
            f"读取现有任务失败，已加载空白模板：{html.escape(str(exc))}"
            "</p>"
        )

    initial_json = _json_for_script(initial_tasks)
    script_template = """
<script>
(function () {
  const root = document.getElementById('batch-edit-root');
  if (!root || root.dataset.inited === '1') {
    return;
  }
  root.dataset.inited = '1';

  const form = root.querySelector('#batch-edit-visual-form');
  const payloadInput = root.querySelector('#batch-edit-visual-payload');
  const taskList = root.querySelector('#batch-task-list');
  const addTaskBtn = root.querySelector('#batch-add-task-btn');
  if (!form || !payloadInput || !taskList || !addTaskBtn) {
    return;
  }

  const initialTasks = __INITIAL_TASKS__;

  function defaultEdit() {
    return {
      mode: 'anchor',
      line_start: '',
      line_end: '',
      from: '',
      to: '',
      action: '',
      inclusive: true,
      pattern: '',
      regex_flags: '',
      regex_count: ''
    };
  }

  function defaultTask() {
    return {
      file: '',
      edits: [defaultEdit()]
    };
  }

  function normalizeTask(rawTask) {
    const task = rawTask && typeof rawTask === 'object' ? rawTask : {};
    const out = {
      file: typeof task.file === 'string' ? task.file : '',
      edits: []
    };

    const edits = Array.isArray(task.edits) && task.edits.length > 0 ? task.edits : [defaultEdit()];
    edits.forEach(function (edit) {
      out.edits.push(Object.assign(defaultEdit(), edit && typeof edit === 'object' ? edit : {}));
    });
    return out;
  }

  function refreshAllLabels() {
    const taskCards = taskList.querySelectorAll('.be-task-card');
    taskCards.forEach(function (taskCard, taskIndex) {
      const title = taskCard.querySelector('.be-task-title');
      if (title) {
        title.textContent = '任务 ' + (taskIndex + 1);
      }

      const editItems = taskCard.querySelectorAll('.be-edit-item');
      editItems.forEach(function (editItem, editIndex) {
        const editTitle = editItem.querySelector('.be-edit-title');
        if (editTitle) {
          editTitle.textContent = '修改文本 ' + (editIndex + 1);
        }
      });
    });
  }

  function updateModeView(editItem) {
    const mode = editItem.querySelector('.be-mode').value;
    const anchorBlock = editItem.querySelector('.be-anchor-block');
    const regexBlock = editItem.querySelector('.be-regex-block');

    anchorBlock.style.display = mode === 'anchor' ? 'block' : 'none';
    regexBlock.style.display = mode === 'regex' ? 'block' : 'none';
  }

  function createEditItem(editData, editsWrap) {
    const data = Object.assign(defaultEdit(), editData && typeof editData === 'object' ? editData : {});

    const editItem = document.createElement('div');
    editItem.className = 'be-edit-item';
    editItem.style.cssText = 'border:1px solid #dbe3ef;border-radius:8px;padding:10px;margin:8px 0;background:#f8fbff;';
    editItem.innerHTML =
      "<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;'>"
      + "<strong class='be-edit-title'>修改文本</strong>"
      + "<button type='button' class='be-remove-edit' style='background:#ef4444;'>删除</button>"
      + "</div>"
      + "<div class='row' style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;'>"
      + "<label>mode<br /><select class='be-mode'><option value='anchor'>anchor</option><option value='regex'>regex</option></select></label>"
      + "<label>line start<br /><input type='number' class='be-line-start' style='width:120px;' /></label>"
      + "<label>line end<br /><input type='number' class='be-line-end' style='width:120px;' /></label>"
      + "</div>"
      + "<div class='be-anchor-block' style='margin-bottom:8px;'>"
      + "<div class='row' style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;'>"
      + "<label>from<br /><input type='text' class='be-from' style='width:260px;' /></label>"
      + "<label>to<br /><input type='text' class='be-to' style='width:260px;' /></label>"
      + "</div>"
      + "<label><input type='checkbox' class='be-inclusive' /> inclusive（是否包含 from/to）</label>"
      + "</div>"
      + "<div class='be-regex-block' style='margin-bottom:8px;'>"
      + "<div class='row' style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;'>"
      + "<label>pattern<br /><input type='text' class='be-pattern' style='width:300px;' /></label>"
      + "<label>regex_flags<br /><input type='text' class='be-regex-flags' placeholder='i/m/s/x' style='width:120px;' /></label>"
      + "<label>regex_count<br /><input type='number' class='be-regex-count' min='0' style='width:120px;' /></label>"
      + "</div>"
      + "</div>"
      + "<div class='row'>"
      + "<label>action（替换内容）<br /><textarea class='be-action' style='width:100%;min-height:70px;padding:6px;border:1px solid #dbe3ef;border-radius:6px;'></textarea></label>"
      + "</div>";

    const modeEl = editItem.querySelector('.be-mode');
    const lineStartEl = editItem.querySelector('.be-line-start');
    const lineEndEl = editItem.querySelector('.be-line-end');
    const fromEl = editItem.querySelector('.be-from');
    const toEl = editItem.querySelector('.be-to');
    const actionEl = editItem.querySelector('.be-action');
    const inclusiveEl = editItem.querySelector('.be-inclusive');
    const patternEl = editItem.querySelector('.be-pattern');
    const regexFlagsEl = editItem.querySelector('.be-regex-flags');
    const regexCountEl = editItem.querySelector('.be-regex-count');

    modeEl.value = data.mode === 'regex' ? 'regex' : 'anchor';
    lineStartEl.value = data.line_start || '';
    lineEndEl.value = data.line_end || '';
    fromEl.value = data.from || '';
    toEl.value = data.to || '';
    actionEl.value = data.action || '';
    inclusiveEl.checked = data.inclusive !== false;
    patternEl.value = data.pattern || '';
    regexFlagsEl.value = data.regex_flags || '';
    regexCountEl.value = data.regex_count || '';

    modeEl.addEventListener('change', function () {
      updateModeView(editItem);
    });

    editItem.querySelector('.be-remove-edit').addEventListener('click', function () {
      const editCount = editsWrap.querySelectorAll('.be-edit-item').length;
      if (editCount <= 1) {
        window.alert('每个任务至少保留一条修改文本。');
        return;
      }
      editItem.remove();
      refreshAllLabels();
    });

    updateModeView(editItem);
    return editItem;
  }

  function createTaskCard(taskData) {
    const task = normalizeTask(taskData);

    const taskCard = document.createElement('div');
    taskCard.className = 'be-task-card';
    taskCard.style.cssText = 'border:1px solid #dbe3ef;border-radius:10px;padding:12px;margin:10px 0;background:#ffffff;';

    taskCard.innerHTML =
      "<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>"
      + "<h4 class='be-task-title' style='margin:0;'>任务</h4>"
      + "<button type='button' class='be-remove-task' style='background:#ef4444;'>删除任务</button>"
      + "</div>"
      + "<div class='row' style='margin-bottom:8px;'>"
      + "<label>文件地址 file<br /><input type='text' class='be-file' style='width:100%;max-width:720px;' /></label>"
      + "</div>"
      + "<div class='be-edits-wrap'></div>"
      + "<button type='button' class='be-add-edit' style='background:#0ea5e9;margin-top:6px;'>新增替换文本</button>";

    const fileEl = taskCard.querySelector('.be-file');
    const editsWrap = taskCard.querySelector('.be-edits-wrap');
    const addEditBtn = taskCard.querySelector('.be-add-edit');

    fileEl.value = task.file || '';

    task.edits.forEach(function (editData) {
      editsWrap.appendChild(createEditItem(editData, editsWrap));
    });

    addEditBtn.addEventListener('click', function () {
      editsWrap.appendChild(createEditItem(defaultEdit(), editsWrap));
      refreshAllLabels();
    });

    taskCard.querySelector('.be-remove-task').addEventListener('click', function () {
      const taskCount = taskList.querySelectorAll('.be-task-card').length;
      if (taskCount <= 1) {
        window.alert('至少保留一个任务。');
        return;
      }
      taskCard.remove();
      refreshAllLabels();
    });

    return taskCard;
  }

  function serializeTasks() {
    const out = [];
    taskList.querySelectorAll('.be-task-card').forEach(function (taskCard) {
      const file = taskCard.querySelector('.be-file').value.trim();
      const edits = [];

      taskCard.querySelectorAll('.be-edit-item').forEach(function (editItem) {
        edits.push({
          mode: editItem.querySelector('.be-mode').value,
          line_start: editItem.querySelector('.be-line-start').value.trim(),
          line_end: editItem.querySelector('.be-line-end').value.trim(),
          from: editItem.querySelector('.be-from').value,
          to: editItem.querySelector('.be-to').value,
          action: editItem.querySelector('.be-action').value,
          inclusive: editItem.querySelector('.be-inclusive').checked,
          pattern: editItem.querySelector('.be-pattern').value,
          regex_flags: editItem.querySelector('.be-regex-flags').value,
          regex_count: editItem.querySelector('.be-regex-count').value.trim()
        });
      });

      out.push({ file: file, edits: edits });
    });
    return out;
  }

  addTaskBtn.addEventListener('click', function () {
    taskList.appendChild(createTaskCard(defaultTask()));
    refreshAllLabels();
  });

  const seedTasks = Array.isArray(initialTasks) && initialTasks.length > 0 ? initialTasks : [defaultTask()];
  seedTasks.forEach(function (task) {
    taskList.appendChild(createTaskCard(task));
  });
  refreshAllLabels();

  form.addEventListener('submit', function (event) {
    const tasks = serializeTasks();
    if (!tasks.length) {
      window.alert('请至少新增一个任务。');
      event.preventDefault();
      return;
    }
    payloadInput.value = JSON.stringify({ tasks: tasks });
  });
})();
</script>
"""
    script_html = script_template.replace("__INITIAL_TASKS__", initial_json)

    return (
        "<h3>任务配置（可视化编辑）</h3>"
        "<p style='margin:8px 0;color:#64748b;'>点击“新增任务”可增加一个文件任务；在任务内点击“新增替换文本”可增加多条修改规则。</p>"
        f"<p style='margin:8px 0;color:#64748b;'>任务文件：<code>{html.escape(tasks_path)}</code></p>"
        f"{load_error_html}"
        "<div id='batch-edit-root'>"
        "<form id='batch-edit-visual-form' method='post' action='/plugin-action' style='margin-bottom:10px;'>"
        f"<input type='hidden' name='plugin_id' value='{html.escape(PLUGIN_ID)}' />"
        "<input type='hidden' name='action' value='save-visual-tasks' />"
        "<input type='hidden' name='visual_tasks_payload' id='batch-edit-visual-payload' />"
        "<div id='batch-task-list'></div>"
        "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;'>"
        "<button type='button' id='batch-add-task-btn' style='background:#0ea5e9;'>新增任务</button>"
        "<button type='submit'>保存任务配置</button>"
        "</div>"
        "</form>"
        f"{script_html}"
        "</div>"
    )


def get_web_module(context: dict) -> dict:
    plugin_dir = _resolve_plugin_dir(context)
    settings = _load_settings(plugin_dir)

    body = (
        "<p>该模块用于批量改文件，支持 <code>anchor</code> 与 <code>regex</code> 两种模式。</p>"
        "<p style='margin:8px 0;color:#64748b;'>启用分辨率变量："
        "<code>{{enabled_width}}</code>、<code>{{enabled_height}}</code>、<code>{{enabled_refresh}}</code>；"
        "可用于 <code>action/new_text/replacement</code> 中。</p>"
        f"{_build_settings_panel(plugin_dir, settings)}"
        "<hr style='margin:16px 0;border:none;border-top:1px solid #dbe3ef;' />"
        f"{_build_tasks_editor_panel(plugin_dir)}"
        "<hr style='margin:16px 0;border:none;border-top:1px solid #dbe3ef;' />"
        "<h3>当前任务预览</h3>"
        f"{_render_rules_table(plugin_dir)}"
        "<p style='margin-top:12px;color:#64748b;'>提示：规则按顺序执行，上一条结果会影响下一条。</p>"
    )

    return {
        "id": PLUGIN_ID,
        "title": PLUGIN_NAME,
        "html": body,
    }


def _form_first(form: Dict, key: str, default: str = "") -> str:
    value = form.get(key, [default])
    if isinstance(value, list):
        return str(value[0]).strip() if value else default
    return str(value).strip()


def _form_has(form: Dict, key: str) -> bool:
    return key in form


def handle_web_action(action: str, form: Dict, context: dict) -> str:
    plugin_dir = _resolve_plugin_dir(context)

    if action == "save-settings":
        settings = PluginSettings(
            enabled=_form_has(form, "enabled"),
            dry_run=_form_has(form, "dry_run"),
            stop_on_error=_form_has(form, "stop_on_error"),
            backup_before_write=_form_has(form, "backup_before_write"),
        )
        path = _save_settings(plugin_dir, settings)
        return f"插件配置已保存：{path}"

    if action == "save-visual-tasks":
        raw_text = _form_first(form, "visual_tasks_payload", "")
        if not raw_text:
            raise ValueError("任务配置不能为空")

        task_count, flat_edits = _parse_visual_tasks_payload(raw_text)
        normalized = {"edits": flat_edits}
        _parse_rules_from_data(normalized)

        path = _save_tasks_data(plugin_dir, normalized)
        return (
            f"任务配置已保存：{path}（任务数: {task_count}，修改文本: {len(flat_edits)}）"
        )

    if action == "save-tasks":
        raw_text = _form_first(form, "tasks_json", "")
        if not raw_text:
            raise ValueError("任务配置不能为空")

        data = _parse_json_text(raw_text)
        normalized = _normalize_tasks_data(data)
        _parse_rules_from_data(normalized)

        path = _save_tasks_data(plugin_dir, normalized)
        return f"任务配置已保存：{path}（规则数: {len(normalized.get('edits', []))}）"

    if action == "run-now":
        base_dir = context.get("base_dir") if isinstance(context, dict) else None
        if not base_dir:
            raise ValueError("缺少 base_dir，无法执行任务")

        settings = _load_settings(plugin_dir)
        total, success_count, failed_count = _execute_rules(
            base_dir,
            plugin_dir,
            settings,
            force_run=True,
            context=context,
        )
        return (
            f"手动执行完成：总任务 {total}，成功 {success_count}，失败 {failed_count}"
            f"（dry_run={settings.dry_run}）"
        )

    raise ValueError(f"未知插件动作: {action}")


def start(context: dict) -> None:
    global _logger
    _logger = context.get("logger") or logging.getLogger(__name__)

    base_dir = context.get("base_dir")
    if not base_dir:
        _logger.warning("插件上下文缺少 base_dir，跳过执行")
        return

    plugin_dir = _resolve_plugin_dir(context)
    settings = _load_settings(plugin_dir)
    total, success_count, failed_count = _execute_rules(
        base_dir,
        plugin_dir,
        settings,
        force_run=False,
        context=context,
    )

    if total > 0:
        _logger.info(
            "[%s] 执行结束：总任务=%d 成功=%d 失败=%d",
            PLUGIN_NAME,
            total,
            success_count,
            failed_count,
        )


def stop() -> None:
    if _logger:
        _logger.info("[%s] 插件停止", PLUGIN_NAME)
