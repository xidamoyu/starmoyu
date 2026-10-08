#!/usr/bin/env bash
# WSL 保活 + MinIO 僵死自愈(每 60s: 保活 sleep 防休眠;探活失败 restart MinIO)
while true; do
  if ! timeout 5 curl -s -o /dev/null http://127.0.0.1:9000/minio/health/live; then
    docker restart starmoyu-minio >/dev/null 2>&1
  fi
  sleep 60
done
