@echo off
chcp 65001 >nul
title AI口播工作台
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo [首次运行] 正在创建虚拟环境并安装依赖，可能需要几分钟...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)
if not exist web\dist\index.html (
  echo [提示] 前端尚未构建，本次启动将显示提示页。可运行 build.bat 构建后刷新
)

:loop
echo [%date% %time%] AI口播工作台启动： http://127.0.0.1:8787
echo 关闭本窗口即可停止服务。若服务意外退出，将自动重启。
start "" http://127.0.0.1:8787
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
echo.
echo [%date% %time%] 服务退出（代码 %errorlevel%），5 秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop
