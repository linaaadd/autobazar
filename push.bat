@echo off
cd /d "C:\Users\llina\my_first_bot"
del /f ".git\index.lock" 2>nul
git -c user.email="llinaaadd@gmail.com" -c user.name="Danylo Lukianchikov" add "My First Bot\bot.py" "My First Bot\requirements.txt" "My First Bot\railway.toml" "My First Bot\webapp\index.html"
git -c user.email="llinaaadd@gmail.com" -c user.name="Danylo Lukianchikov" commit -m "Add Telegram Mini App (webapp) + aiohttp web server"
git push
echo Done! Press any key to close.
pause
