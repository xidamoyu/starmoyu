#!/usr/bin/env bash
# starmoyu 一键关闭（git-bash / MSYS）：停后端 :8000 + 前端 :5173 + 残留 node/vite
# 用法：bash stop.sh
# 注意：不停 WSL 容器（Milvus/MinIO 常驻无害，且有 keepalive 任务防休眠）；
#       如需连容器一起停，bash stop.sh --with-wsl

set +e  # 逐项清理,单项失败不中断

echo "[1/3] 停止后端 FastAPI :8000 ..."
for PID in $(netstat -ano | grep ":8000" | grep LISTENING | awk '{print $NF}' | sort -u); do
  echo "    结束进程 $PID"
  taskkill /F /PID "$PID" >/dev/null 2>&1
done

echo "[2/3] 停止前端 Vite :5173 ..."
for PID in $(netstat -ano | grep ":5173" | grep LISTENING | awk '{print $NF}' | sort -u); do
  echo "    结束进程 $PID"
  taskkill /F /PID "$PID" >/dev/null 2>&1
done
# Vite 的 esbuild.exe 子进程有时不随端口释放,兜底清一次
taskkill /F /IM esbuild.exe >/dev/null 2>&1

echo "[3/3] 复核..."
sleep 2
LEFT8000=$(netstat -ano | grep ":8000" | grep LISTENING | wc -l)
LEFT5173=$(netstat -ano | grep ":5173" | grep LISTENING | wc -l)
echo "    8000 残留监听: $LEFT8000   5173 残留监听: $LEFT5173"

if [ "$1" = "--with-wsl" ]; then
  echo "[附加] 停止 WSL 容器 + keepalive ..."
  wsl -d Ubuntu -e bash -c "docker stop smartrecruit-milvus smartrecruit-etcd starmoyu-minio" 2>/dev/null \
    && echo "    容器已停止" \
    || echo "    警告：容器停止失败,可进 WSL 手动 docker ps 检查"
  schtasks /End /TN "starmoyu-wsl-keepalive" >/dev/null 2>&1 \
    && echo "    keepalive 任务已暂停（下次 schtasks /Run 或重新登录自动恢复）"
else
  echo ""
  echo "WSL 容器保持运行（keepalive 常驻）,仅停了宿主侧服务。"
  echo "如需连容器一起停：bash stop.sh --with-wsl"
fi

echo ""
echo "================================================"
if [ "$LEFT8000" -eq 0 ] && [ "$LEFT5173" -eq 0 ]; then
  echo "  全部服务已停止"
else
  echo "  注意:仍有端口未释放,手动检查 netstat -ano | grep -E ':8000|:5173'"
fi
echo "  重新启动：bash start.sh"
echo "================================================"
