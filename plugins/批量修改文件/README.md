# 批量修改文件插件

插件：批量修改文件插件，用于批量修改文件内容，可以指定多个文件和替换规则，支持正则表达式匹配。

该目录作为 `GameResolutionService/本体` 的外部扩展插件。

## 插件运行方式
- 本体启动后，会自动把 `GameResolutionService/plugins/*` 导入到 `GameResolutionService/本体/plugins/*`。
- 本体插件系统会自动扫描 `plugin.py` 并执行 `start(context)`。
- 本插件在启动时读取 `tasks.json` 并按顺序执行所有编辑任务。

## 配置页面集成
本插件实现了 `get_web_module(context)`，会在配置页面左侧“模块选择”下拉菜单中显示：
- 模块名称：`批量修改文件`
- 展示方式：在当前网页内切换显示（不打开新网页）

### 插件配置栏
插件页面新增“插件配置”表单，保存到 `plugin_config.json`，支持以下行为开关：
- `enabled`：是否启用插件自动执行（服务启动时）
- `dry_run`：仅演练，不写入文件
- `stop_on_error`：任务失败时停止后续任务
- `backup_before_write`：写入前生成 `.bak` 备份

并提供“立即执行一次任务”按钮，用当前配置手动执行。

### 任务配置可视化编辑
插件页面新增“任务配置（可视化编辑）”：
- 可在网页中“新增任务 / 新增替换文本”进行编辑
- 点击“保存任务配置”时会校验规则字段合法性
- 校验通过后写回 `tasks.json`

### 启用分辨率变量（模板变量）
可在任务文本中直接使用以下变量（双大括号语法）：
- `{{enabled_width}}`：基础配置里的“启用分辨率-宽度”
- `{{enabled_height}}`：基础配置里的“启用分辨率-高度”
- `{{enabled_refresh}}`：基础配置里的“启用分辨率-刷新率”

支持字段：
- `anchor` 模式：`new_text`
- `regex` 模式：`replacement`
- 可视化编辑器中统一对应 `action`

示例：
```json
{
  "file": "SomeGame.cfg",
  "mode": "regex",
  "pattern": "(?m)^ResolutionSizeX\\s*=\\s*\\d+\\s*$",
  "replacement": "ResolutionSizeX={{enabled_width}}",
  "regex_flags": "m",
  "regex_count": 1
}
```

## 任务格式（tasks.json）
```json
{
  "edits": [
    {
      "file": "config.ini",
      "mode": "anchor",
      "line_range": [1, 40],
      "from": "[Enabled]",
      "to": "RefreshRate = 240",
      "inclusive": false,
      "new_text": "\nResolution = 2560 * 1440\nRefreshRate = 240\n"
    },
    {
      "file": "README.md",
      "mode": "regex",
      "line_range": [1, 200],
      "pattern": "(?m)^- 模块名称：.*$",
      "replacement": "- 模块名称：`批量修改文件`",
      "regex_flags": "m",
      "regex_count": 1
    }
  ]
}
```

## 字段说明
- `file`：相对本体目录的目标文件路径。
- `mode`：规则模式，支持：
  - `anchor`：锚点替换模式（`from/to`）
  - `regex`：正则替换模式（`pattern/replacement`）

### `anchor` 模式字段
- `line_range`：只在该行范围中查找并替换（可选，不填表示全文件）。
- `from` / `to`：替换区间锚点。
- `inclusive`：
  - `true`：连同 `from` 和 `to` 一并替换
  - `false`：只替换中间内容
- `new_text`：替换后的文本。

### `regex` 模式字段
- `line_range`：只在该行范围中进行正则替换（可选，不填表示全文件）。
- `pattern`：正则表达式。
- `replacement`：替换内容（支持 `\1` 等分组引用）。
- `regex_flags`：正则标记，支持 `i/m/s/x` 组合。
- `regex_count`：最多替换次数，`0` 表示不限制。

## 注意事项
- 目标文件建议使用 UTF-8。
- 规则按 `edits` 顺序执行，前一条规则的结果会影响后一条规则。
- 若未找到锚点、正则无匹配或行范围非法，会在本体日志中记录错误。
