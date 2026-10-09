@echo off
rem starmoyu 一键关闭（Windows 双击可用）：停后端 :8000 + 前端 :5173
echo [1/3] 停止后端 FastAPI :8000 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo     结束进程 %%p
  taskkill /F /T /PID %%p >nul 2>&1
)
echo [2/3] 停止前端 Vite :5173 ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do (
  echo     结束进程 %%p
  taskkill /F /T /PID %%p >nul 2>&1
)
taskkill /F /IM esbuild.exe >nul 2>&1
echo [3/3] 复核...
timeout /t 2 /nobreak >nul
set LEFT=0
for /f %%p in ('netstat -ano ^| findstr ":8000 :5173" ^| findstr "LISTENING"') do set /a LEFT+=1
if %LEFT%==0 (echo   全部服务已停止) else (echo   注意: 仍有 %LEFT% 个监听未释放, 可手动 taskkill /F /PID 进程号)
echo   重新启动: 双击 start.bat 或 bash start.sh
pause
