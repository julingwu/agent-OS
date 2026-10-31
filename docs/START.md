# 日常只用根目录的 Start.cmd

双击 `D:\Agent-OS\Start.cmd`。

它会检查网页后台、自动启动本机执行程序，确认就绪后打开浏览器。
不用另外找后端、双轨、daemon 或 open-web 启动脚本。

- 后台正常：直接复用并打开网页，不重复启动。
- 旧版、代码更新或服务异常：结束确认属于本项目的旧进程，重新启动。
- 正在执行任务或发布：保留现有服务并打开网页；任务结束后再次点击 Start 加载更新。
- 连续双击：启动锁保证不会同时启动两套后台。
- 其他软件占用端口：明确报错，保留其他软件。
- 失败：窗口保留错误；日志在 `logs/`。

## 根目录

| 入口/目录 | 用途 |
| --- | --- |
| Start.cmd | 唯一日常启动入口 |
| program/ | 程序、前端、开发和测试代码；通常无需打开 |
| data/ | 数据库、启动锁和运行状态 |
| workspace/ | Agent 工作文件 |
| outputs/ | 输出和验证报告 |
| logs/ | 后台日志 |
| docs/ | 说明和历史文档 |
| releases/ | 打包版本 |

`.venv`、`.github` 和以点开头的文件是环境及仓库配置。
`program/` 内有指向这些数据目录的 Windows 目录联接，供旧代码保持兼容；不是重复备份，请不要删除或移动联接目标。

只有维护时才用 `program/Stop.cmd`（停止本项目后台）和 `program/Verify.cmd`（隔离验证）。
私密输入入口位于 `program/Private-input.cmd`。
旧启动脚本已归档到 `docs/archive/launchers/`，不再作为操作入口。

本次没有重新打包旧 EXE/APK，Start 统一使用最新浏览器界面。
不要在任务执行期间使用维护用 Stop。

缺少 Python 环境时，在项目根目录执行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r program\requirements.txt
```

部署/开发命令的工作目录改为 `program/`；原有数据目录保持原位。
