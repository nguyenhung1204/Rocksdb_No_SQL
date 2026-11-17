@echo off
chcp 65001 >nul
setlocal EnableExtensions
title LogStore demo - đóng cửa sổ này để tắt ứng dụng
cd /d "%~dp0"

call :load_env
call :find_python || goto :fail

if not exist ".env.demo" (
  "%PY%" -c "import secrets; open('.env.demo','w',encoding='utf-8').write('# Cau hinh rieng cua may nay - KHONG dua len GitHub\nLOGSTORE_INGEST_TOKEN=' + secrets.token_urlsafe(24) + '\n')"
  call :load_env
)
if not defined LOGSTORE_PORT set "LOGSTORE_PORT=5000"

if not exist "data\webdb\CURRENT" goto :create
echo.
echo  [1] Chạy demo với dữ liệu hiện có
echo  [2] Tạo lại dữ liệu demo mới (xóa CSDL, bản sao lưu cũ) rồi chạy
echo.
choice /c 12 /n /m "Chọn 1 hoặc 2: "
if errorlevel 2 goto :create
goto :run

:create
echo.
echo Đang tạo CSDL demo mới với dữ liệu tính đến thời điểm hiện tại...
"%PY%" tao_du_lieu_demo.py --reset || goto :fail

:run
echo.
echo  LogStore đang chạy tại http://127.0.0.1:%LOGSTORE_PORT%
echo  Đóng cửa sổ này (hoặc Ctrl+C) để tắt ứng dụng.
echo.
"%PY%" app.py
pause
goto :eof

:load_env
rem Đọc KEY=VALUE từ .env.demo (mật khẩu demo, token ingest...); dòng bắt đầu bằng # bị bỏ qua.
if exist ".env.demo" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env.demo") do set "%%A=%%B"
goto :eof

:find_python
if defined LOGSTORE_PYTHON if exist "%LOGSTORE_PYTHON%" (set "PY=%LOGSTORE_PYTHON%" & goto :check_deps)
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto :check_deps
echo Đang tạo môi trường Python riêng cho dự án (chỉ làm lần đầu)...
py -3.12 -m venv .venv >nul 2>&1 || py -3 -m venv .venv >nul 2>&1 || python -m venv .venv >nul 2>&1
if not exist "%PY%" (
  echo Không tìm thấy Python 3.12. Hãy cài Python 3.12 64-bit từ python.org rồi chạy lại,
  echo hoặc đặt LOGSTORE_PYTHON=duong\dan\python.exe trong tệp .env.demo.
  exit /b 1
)
:check_deps
"%PY%" -c "import rocksdict, flask, waitress" >nul 2>&1
if not errorlevel 1 exit /b 0
echo Đang cài thư viện cần thiết (rocksdict, Flask, Waitress)...
"%PY%" -m pip install -r requirements.txt || exit /b 1
exit /b 0

:fail
echo.
echo Không chạy được demo. Xem thông báo lỗi ở trên.
pause
exit /b 1
