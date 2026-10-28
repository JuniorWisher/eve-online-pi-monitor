@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   打包成独立 exe
echo ============================================
set PY=.venv\Scripts\python.exe

if not exist "%PY%" (
    echo [错误] 还没创建运行环境，请先双击 启动.bat
    pause
    exit /b 1
)

echo [1/2] 检查 PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    "%PY%" -m pip install pyinstaller -q
)

echo [2/2] 开始打包（首次约 1-2 分钟）...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "行星工业监视器" ^
    --icon app.ico ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "config.example.json;." ^
    --add-data "app.ico;." ^
    --hidden-import pystray._win32 ^
    app.py

if errorlevel 1 (
    echo.
    echo [失败] 打包出错，请把上面的报错发出来
    pause
    exit /b 1
)

echo.
echo 完成！exe 在 dist\行星工业监视器.exe
echo 首次运行会在 exe 旁边自动生成 config.json 和 data 文件夹
pause
