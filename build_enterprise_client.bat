@echo off
setlocal
cd /d "%~dp0"

REM =============================================================================
REM 企业版 Windows 客户端 — 打成 EXE，员工解压后双击即可（需同目录 config.json）
REM
REM 执行位置：任意一台已安装 Python 3.10+ 的 Windows 电脑，在本项目根目录双击本 bat，
REM           或在 CMD 中先 cd 到 jiankong 根目录再运行。
REM
REM 首次请先（在 CMD 中，于本目录下执行）：
REM   python -m venv .venv-client
REM   .venv-client\Scripts\activate
REM   pip install -r client\requirements-client.txt pyinstaller
REM =============================================================================

if "%~1"=="" (
  echo.
  echo 缺少参数。用法：
  echo   build_enterprise_client.bat http://你的服务器公网IP:8000
  echo.
  echo 示例：
  echo   build_enterprise_client.bat http://119.45.44.95:8000
  echo.
  pause
  exit /b 1
)

set "API_URL=%~1"

call .venv-client\Scripts\activate.bat 2>nul
if errorlevel 1 (
  echo 未找到 .venv-client，请先创建虚拟环境并安装依赖：
  echo   python -m venv .venv-client
  echo   .venv-client\Scripts\activate
  echo   pip install -r client\requirements-client.txt pyinstaller
  pause
  exit /b 1
)

echo 正在打包...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name EnterpriseDouyinClient ^
  --distpath dist ^
  --workpath build\pyi_enterprise_client ^
  --specpath client ^
  --collect-all plyer ^
  client\windows_client.py

if errorlevel 1 (
  echo PyInstaller 失败。
  pause
  exit /b 1
)

python -c "import json,sys; u=sys.argv[1].rstrip('/'); open(r'dist\\EnterpriseDouyinClient\\config.json','w',encoding='utf-8').write(json.dumps({'api_base':u},ensure_ascii=False,indent=2)+chr(10))" "%API_URL%"

copy /Y "client\README_employee.txt" "dist\EnterpriseDouyinClient\" >nul

if errorlevel 1 (
  echo 写入 config.json 失败。
  pause
  exit /b 1
)

echo.
echo 完成。请将整个文件夹打包成 zip 发给员工：
echo   dist\EnterpriseDouyinClient\
echo.
echo 员工操作：解压后双击 EnterpriseDouyinClient.exe，用管理员分配的账号登录即可。
pause
