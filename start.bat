@echo off
chcp 65001 >nul
title 中渔小助 · 一键启动

rem 检查后端虚拟环境是否存在
if not exist "D:\TYKKKKKK\.venvs\marketing-agent\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境：D:\TYKKKKKK\.venvs\marketing-agent
    echo 请先按 README「快速开始」创建 venv 并安装依赖。
    pause
    exit /b 1
)

echo 正在启动后端（端口 8010）...
start "mkt-backend" /D "%~dp0backend" cmd /k D:\TYKKKKKK\.venvs\marketing-agent\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010

echo 正在启动前端（端口 5173）...
start "mkt-frontend" /D "%~dp0frontend" cmd /k npm run dev

echo.
echo 后端 API 文档：http://127.0.0.1:8010/docs
echo 运营后台    ：http://127.0.0.1:5173
echo 提示：关闭弹出的两个窗口即可停止服务，8 秒后自动打开浏览器...

timeout /t 8 >nul
start http://127.0.0.1:5173
