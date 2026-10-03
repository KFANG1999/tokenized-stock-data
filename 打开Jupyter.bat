@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在同步最新数据...
git pull -q
echo 正在打开 JupyterLab（浏览器会自动打开，用完关掉这个窗口即可）...
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m jupyterlab
