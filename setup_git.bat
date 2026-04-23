@echo off
echo Initializing Git...
echo "# telegramBot" > README.md
git init
git add .
git commit -m "Initial commit: Force Join Multi-Bot Cloner"
git branch -M main
git remote add origin https://github.com/PrethwiMe/telegramBot.git
echo.
echo Ready to push. Ensure you are logged into Git.
echo Running: git push -u origin main
git push -u origin main
pause
