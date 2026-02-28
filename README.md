# GameResolutionService 本体（服务与配置界面分离）

本体目录：`GameResolutionService/本体`

## 你要的能力
- 通过网页修改运行配置（进程名、分辨率、开关项）。
- 配置程序双击后自动打开浏览器进入配置页。
- 本体提供插件接口，自动加载并启动扩展插件。
- 配置页面左侧可切换“基础配置 / 插件模块”，同页切换不打开新网页。
- 支持外部插件目录：`GameResolutionService/plugins/*`。
- 启动方式分离：服务启动 与 配置界面启动独立。

## 启动方式
1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 仅启动服务（后台）：
   - 双击 `StartService.bat`
   - 等价命令：
     ```bash
     python main.py --daemon
     ```
3. 仅启动配置界面（前台 + 自动开浏览器）：
   - 双击 `StartConfigUI.bat`
   - 等价命令：
     ```bash
     python main.py --config-ui
     ```
4. 默认网页地址：`http://127.0.0.1:8765`

## 网页配置项
- 启动器进程名
- 游戏进程名
- 默认分辨率（退回）
- 启用分辨率（游戏中）
- 启动时记录当前分辨率
- 启用插件系统

网页保存后，会写回 `config.ini`。

## 插件接口
本体会扫描 `本体/plugins/*/plugin.py`。

插件可提供：
- `start(context: dict)`（必选）
- `stop()`（可选）
- `PLUGIN_NAME = "xxx"`（可选）
- `PLUGIN_ID = "xxx"`（可选，网页模块 ID）
- `get_web_module(context: dict) -> dict`（可选，注册网页模块）

`get_web_module` 返回示例：
```python
{
  "id": "batch-edit",
  "title": "批量修改文件",
  "html": "<p>插件模块内容</p>",
}
```

`context` 常用字段：
- `base_dir`：本体目录
- `plugins_dir`：本体插件目录
- `config`：当前配置对象
- `config_path`：配置文件路径
- `logger`：本体日志器


## 现成插件：批量修改文件
目录：`GameResolutionService/plugins/批量修改文件`

- 入口：`plugin.py`
- 配置：`tasks.json`
- 文档：`README.md`

该插件支持你要求的模式：
- 在第 `n-m` 行范围内
- 从 `from` 到 `to` 锚点
- 进行段落替换
- 支持批量任务 `edits[]`
- 支持在配置页中作为独立模块展示

## 日志
本体日志目录：`GameResolutionService/本体/Logs/YYYY-MM-DD/HH-MM-SS/log.txt`
