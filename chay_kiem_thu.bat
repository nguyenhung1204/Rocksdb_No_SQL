@echo off
chcp 65001 >nul
setlocal
title LogStore - chạy kiểm thử
cd /d "%~dp0"
if exist ".env.demo" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env.demo") do set "%%A=%%B"
set "PY=%~dp0.venv\Scripts\python.exe"
if defined LOGSTORE_PYTHON set "PY=%LOGSTORE_PYTHON%"
if not exist "%PY%" (
  echo Hãy chạy chay_demo.bat một lần để tạo môi trường Python trước.
  pause
  exit /b 1
)
echo Lưu ý: tắt cửa sổ LogStore đang chạy trước khi kiểm thử (tránh khóa LOCK của RocksDB).
echo.
"%PY%" -m unittest discover -s tests -v > kiem_thu_ket_qua.txt 2>&1
set "RC=%ERRORLEVEL%"
powershell -NoProfile -Command "Get-Content -Encoding UTF8 kiem_thu_ket_qua.txt -Tail 4"
if "%RC%"=="0" (echo TẤT CẢ KIỂM THỬ ĐẠT.) else (echo CÓ KIỂM THỬ LỖI - xem kiem_thu_ket_qua.txt)
pause
