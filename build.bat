@echo off
chcp 65001 >nul
cd /d %~dp0
echo 正在构建前端（需要 Node.js）...
cd web
call npm install --no-audit --no-fund
call npm run build
if exist dist\index.html (echo 构建完成，运行 启动.bat 即可使用) else (echo 构建失败，请检查 Node 环境)
pause
