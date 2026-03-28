@echo off
setlocal

REM 在 Windows 下运行：构建监控软件安装包（EXE）
REM 1) python -m venv .venv
REM 2) .venv\Scripts\activate
REM 3) pip install -r requirements.txt pyinstaller

pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name "DouyinLikeMonitor" ^
  "douyin_monitor_gui.py"

echo.
echo 构建完成，EXE 位于 dist\DouyinLikeMonitor\DouyinLikeMonitor.exe
pause
