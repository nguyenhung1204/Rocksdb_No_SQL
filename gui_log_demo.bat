@echo off
chcp 65001 >nul
setlocal EnableExtensions
title LogStore - gửi log từ ứng dụng khác
cd /d "%~dp0"
if exist ".env.demo" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env.demo") do set "%%A=%%B"
if not defined LOGSTORE_PORT set "LOGSTORE_PORT=5000"
set "LOGSTORE_URL=http://127.0.0.1:%LOGSTORE_PORT%"
set "PY=%~dp0.venv\Scripts\python.exe"
if defined LOGSTORE_PYTHON set "PY=%LOGSTORE_PYTHON%"
if not exist "%PY%" (
  echo Hãy chạy chay_demo.bat trước để tạo môi trường Python và khởi động LogStore.
  pause
  exit /b 1
)
echo === Lần 1: ứng dụng checkout-service gửi 4 sự kiện của trace ORDER-2026-1042 ===
"%PY%" examples\emit_logs.py
echo.
echo === Lần 2: gửi lại đúng 4 sự kiện đó (mô phỏng gửi lại khi mất kết nối) ===
"%PY%" examples\emit_logs.py
echo.
echo Mở trang Sự kiện, nhập Trace ID = ORDER-2026-1042 để xem 4 sự kiện (không bị nhân đôi).
pause
