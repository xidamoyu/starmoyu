# 同步 WSL 容器端口的 portproxy 转发(WSL2 NAT relay 在 Win10 上会静默失效)
$ErrorActionPreference = "Stop"
$ip = (wsl -d Ubuntu -e bash -c "hostname -I").Trim().Split(" ")[0]
if (-not $ip -match '^\d+\.\d+\.\d+\.\d+$') { exit 1 }
foreach ($port in 9000, 19530) {
    $existing = netsh interface portproxy show v4tov4 | Select-String "127\.0\.0\.1\s+$port\s+(\S+)"
    if ($existing -and $existing.Matches[0].Groups[1].Value -eq $ip) { continue }
    netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$port 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$port connectaddress=$ip connectport=$port | Out-Null
}
