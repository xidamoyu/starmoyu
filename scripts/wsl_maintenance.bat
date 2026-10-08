@echo off
cd /d C:\Users\Administrator\AppData\Local\hermes\workspace\starmoyu
powershell -ExecutionPolicy Bypass -File scripts\wsl_portproxy_sync.ps1 >nul 2>&1
wsl -d Ubuntu -e bash -c "timeout 4 curl -s -o /dev/null http://127.0.0.1:9000/minio/health/live || docker restart starmoyu-minio" >nul 2>&1
