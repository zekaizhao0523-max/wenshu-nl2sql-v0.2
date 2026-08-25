# 启动本机 MySQL 8.0(3307) + Qdrant(6333)。数据在 F:\wenshu-local，不改项目 .env。
$ErrorActionPreference = "Stop"
$Local = "F:\wenshu-local"
$Mysqld = Join-Path $Local "mysql\mysql-8.0.46-winx64\bin\mysqld.exe"
$Qdrant = Join-Path $Local "qdrant\qdrant.exe"
$Ini = Join-Path $Local "my.ini"

if (-not (Test-Path $Mysqld)) { throw "缺少 $Mysqld" }
if (-not (Test-Path $Qdrant)) { throw "缺少 $Qdrant" }
if (-not (Test-Path $Ini)) { throw "缺少 $Ini" }

if (-not (Get-NetTCPConnection -LocalPort 3307 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host "starting MySQL 8.0 on 3307..."
    Start-Process -FilePath $Mysqld -ArgumentList "--defaults-file=$Ini" -WorkingDirectory (Split-Path (Split-Path $Mysqld)) -WindowStyle Hidden
} else {
    Write-Host "mysqld already listening on 3307"
}

if (-not (Get-NetTCPConnection -LocalPort 6333 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host "starting qdrant on 6333..."
    Start-Process -FilePath $Qdrant -WorkingDirectory (Split-Path $Qdrant) -WindowStyle Hidden
} else {
    Write-Host "qdrant already listening on 6333"
}

Write-Host "local stack requested. mysql=3307 qdrant=6333"
