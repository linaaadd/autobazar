@echo off
cd /d "C:\Users\llina\my_first_bot"
del /f ".git\index.lock" 2>nul
git -c user.email="llinaaadd@gmail.com" -c user.name="Danylo Lukianchikov" commit -a -m "Fix railway.toml duplicate keys"
git push
echo.
echo Done! Press any key to close.
pause
