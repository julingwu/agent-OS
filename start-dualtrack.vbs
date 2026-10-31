Set WshShell = CreateObject("WScript.Shell")
Dim fso, scriptDir
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' 静默启动双轨调度管理器 (pythonw 无窗口模式)
WshShell.Run """" & scriptDir & "\.venv\Scripts\pythonw.exe"" """ & scriptDir & "\supervisor.py""", 0, False

' 稍等 1.5 秒后端与网关就绪，自动打开浏览器
WScript.Sleep 1800
WshShell.Run "http://127.0.0.1:8077/ui/", 1, False
