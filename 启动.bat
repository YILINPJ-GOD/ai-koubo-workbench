@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo [首次运行] 正在创建虚拟环境并安装依赖，可能需要几分钟...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)
if not exist web\dist\index.html (
  echo [提示] 前端尚未构建，本次启动后端将显示提示页。请先运行 build.bat
)
echo 正在启动 AI口播工作台： http://127.0.0.1:8787
start "" http://127.0.0.1:8787
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
