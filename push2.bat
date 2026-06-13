@echo off
cd /d "C:\Users\llina\my_first_bot"
del /f ".git\index.lock" 2>nul
git -c user.email="llinaaadd@gmail.com" -c user.name="Danylo Lukianchikov" add "My First Bot\webapp\index.html" 2>nul
git -c user.email="llinaaadd@gmail.com" -c user.name="Danylo Lukianchikov" commit -a -m "Add Telegram Mini App (webapp) + aiohttp web server"
git push
echo.
echo Done! Press any key to close.
pause
