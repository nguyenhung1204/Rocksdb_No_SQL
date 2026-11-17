@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where wsl >nul 2>&1 || (
  echo Máy chưa có WSL. Cài bằng lệnh: wsl --install -d Ubuntu-24.04  ^(chạy PowerShell quyền Administrator^)
  echo Sau đó trong Ubuntu chạy: sudo apt install rocksdb-tools
  pause
  exit /b 1
)
wsl --cd "%~dp0" -- sh -c "command -v ldb >/dev/null || { echo 'Hay cai: sudo apt install rocksdb-tools'; exit 1; }; B=$(ls -d data/backups/backup-* 2>/dev/null | tail -1); [ -n \"$B\" ] || { echo 'Chua co ban sao luu trong data/backups'; exit 1; }; sh scripts/ldb_demo.sh \"$B\""
pause
