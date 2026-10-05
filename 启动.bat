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

rem 已有实例在运行：只打开页面，不重复起服务（修：无限弹浏览器）
netstat -ano | findstr /c:":8787" | findstr "LISTENING" >nul
if %errorlevel%==0 (
  echo [提示] 检测到服务已在运行，直接打开页面。
  start "" http://127.0.0.1:8787
  timeout /t 3 /nobreak >nul
  exit /b 0
)

rem 浏览器只打开一次（看护循环重启服务时不再弹窗）
start "" http://127.0.0.1:8787

:loop
echo [%date% %time%] AI口播工作台运行中： http://127.0.0.1:8787
echo 关闭本窗口即可停止服务。若服务意外退出，5 秒后自动重启（不再重复弹浏览器）。
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
echo.
echo [%date% %time%] 服务退出（代码 %errorlevel%），5 秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop
