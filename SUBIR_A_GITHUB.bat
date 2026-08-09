@echo off
chcp 65001 >nul
title Subir proyecto a GitHub
cd /d "%~dp0"

echo ============================================================
echo   Subiendo el proyecto a GitHub (landing-presupuesto)
echo ============================================================
echo.

git add -A
git commit -m "Subida inicial a GitHub"

git remote remove origin >nul 2>nul
git remote add origin https://github.com/leomerino-netizen/landing-presupuesto.git
git branch -M main
git push -u origin main

echo.
echo ============================================================
echo   Si se ha abierto el navegador pidiendo autorizar GitHub,
echo   dale a "Authorize" y luego vuelve aqui y pulsa una tecla.
echo   Si arriba pone algo con "main -> main" ya esta subido.
echo ============================================================
pause
