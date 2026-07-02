@echo off
chcp 65001 >nul
title Asistente Imprimir Libro - Tunel publico
cd /d "%~dp0"

echo ============================================================
echo   Compartir el asistente online (tunel Cloudflare)
echo ============================================================
echo.

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo  cloudflared NO esta instalado. Instalalo una vez con:
  echo.
  echo      winget install --id Cloudflare.cloudflared
  echo.
  echo  Luego vuelve a ejecutar este archivo.
  pause
  exit /b 1
)

echo  1) Asegurate de que el asistente esta arrancado
echo     (doble clic en ABRIR_ASISTENTE.bat).
echo.
echo  2) Abajo aparecera una URL https://....trycloudflare.com
echo     Esa es la direccion publica: abrela en el movil o
echo     compartela. Funciona mientras este PC y el asistente
echo     sigan encendidos.
echo.
echo ------------------------------------------------------------
echo.

cloudflared tunnel --url http://localhost:8765
pause
