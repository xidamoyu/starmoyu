' 隐藏窗口运行计划任务(避免每5分钟弹黑窗)
CreateObject("Wscript.Shell").Run """" & WScript.Arguments(0) & """", 0, False
