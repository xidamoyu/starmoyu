@echo off
rem ============================================================
rem starmoyu 一键启动：WSL 容器(Milvus/MinIO) + 后端 :8000 + 前端 :5173
rem 双击运行或命令行执行；两个窗口分别承载前后端，关闭窗口即停止对应服务。
rem ============================================================
setlocal
set ROOT=%~dp0
set LOGDIR=%ROOT%reports
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo [1/3] 拉起 WSL 容器（etcd / Milvus / MinIO）...
wsl -d Ubuntu -e bash -c "docker start smartrecruit-etcd smartrecruit-milvus starmoyu-minio" >nul 2>&1
if errorlevel 1 (
    echo     警告：WSL 容器拉起失败，检查 Docker 是否在 Ubuntu 内运行
) else (
    echo     容器已就绪（Milvus 启动约需 20-40 秒）
)

rem WSL2 闲置约15秒会自休眠导致 Milvus/MinIO 端口失联——开一个常驻保活窗口
echo     WSL 保活窗口已开（关闭它会中断向量库连接）
start "starmoyu-wsl-keepalive" /min cmd /k "wsl -d Ubuntu -e bash -c ""while true; do sleep 60; done"""

echo [2/3] 启动后端 FastAPI :8000 ...
start "starmoyu-backend" cmd /k "cd /d %ROOT% && set LANGGRAPH_CHECKPOINT_SQLITE=%ROOT%data\checkpoints.db && set PYTHONUNBUFFERED=1 && set PYTHONIOENCODING=utf-8 && .venv\Scripts\python.exe -m uvicorn src.server.api:app --host 0.0.0.0 --port 8000"

echo [3/3] 启动前端 Vite :5173 ...
start "starmoyu-frontend" cmd /k "cd /d %ROOT%frontend && npm run dev"

echo.
echo 等待服务就绪...
timeout /t 12 /nobreak >nul

curl -s -m 10 http://127.0.0.1:8000/api/health > "%LOGDIR%\health.json" 2>nul
findstr /C:"\"ok\":true" "%LOGDIR%\health.json" >nul 2>&1
if errorlevel 1 (
    echo [!] 后端未就绪或基础设施异常，详见 %LOGDIR%\health.json 与 backend.log
) else (
    echo [OK] 后端健康检查通过（三存储状态见 %LOGDIR%\health.json）
)

echo.
echo ================================================
echo   前端  http://localhost:5173
echo   后端  http://localhost:8000  (API 文档 /docs)
echo   健康  http://127.0.0.1:8000/api/health
echo   关闭：直接关掉两个 starmoyu-* 窗口
echo ================================================
endlocal
