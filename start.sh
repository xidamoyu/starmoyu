#!/usr/bin/env bash
# starmoyu 一键启动（git-bash / MSYS）：WSL 容器 + 后端 :8000 + 前端 :5173
# 用法：bash start.sh ；停止：Ctrl+C 或 kill 后台 PID
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGDIR="$ROOT/reports"
mkdir -p "$LOGDIR"

echo "[0/3] 清理残留的 8000 端口进程（上次可能留下僵死后端）..."
for PID in $(netstat -ano | grep ":8000" | grep LISTENING | awk '{print $NF}' | sort -u); do
  echo "    结束残留进程 $PID"
  taskkill //F //PID "$PID" >/dev/null 2>&1 || true
done

echo "[1/3] 拉起 WSL 容器（etcd / Milvus / MinIO）..."
wsl -d Ubuntu -e bash -c "docker start smartrecruit-etcd smartrecruit-milvus starmoyu-minio" 2>/dev/null \
  && echo "    容器已就绪（Milvus 启动约需 20-40 秒）" \
  || echo "    警告：WSL 容器拉起失败，检查 Docker 是否在 Ubuntu 内运行"

echo "[2/3] 启动后端 FastAPI :8000 ..."
cd "$ROOT"
export LANGGRAPH_CHECKPOINT_SQLITE="$ROOT/data/checkpoints.db"
export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe -m uvicorn src.server.api:app --host 0.0.0.0 --port 8000 > "$LOGDIR/backend.log" 2>&1 &
BACKEND_PID=$!

echo "[3/3] 启动前端 Vite :5173 ..."
cd "$ROOT/frontend"
npm run dev > "$LOGDIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT

echo ""
echo "等待服务就绪..."
sleep 12
if curl -s -m 10 http://127.0.0.1:8000/api/health | grep -q '"ok":true'; then
  echo "[OK] 后端健康检查通过"
else
  echo "[!] 后端未就绪，详见 $LOGDIR/backend.log"
fi

echo ""
echo "================================================"
echo "  前端  http://localhost:5173"
echo "  后端  http://localhost:8000  (API 文档 /docs)"
echo "  健康  http://127.0.0.1:8000/api/health"
echo "  停止：Ctrl+C（同时结束前后端）"
echo "================================================"
wait
