@echo off
title Instalador do agente de cameras
cd /d "%~dp0"
if not exist "%~dp0instalar.ps1" goto compactado
if not exist "%~dp0config.json" goto compactado

net session >nul 2>&1
if errorlevel 1 goto pedir_admin

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
echo Pode fechar esta janela.
pause >nul
exit /b

:pedir_admin
echo Pedindo permissao de administrador...
set "ESTE_ARQUIVO=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath $env:ESTE_ARQUIVO -Verb RunAs } catch { exit 1 }"
if not errorlevel 1 exit /b
echo.
echo O Windows nao deu a permissao de administrador.
echo Clique com o botao direito em instalar.bat e escolha "Executar como administrador".
echo Se pedir senha, chame quem administra este computador.
echo.
pause
exit /b 1

:compactado
echo.
echo Este arquivo foi aberto de dentro do ZIP, sem descompactar.
echo.
echo 1. Feche esta janela.
echo 2. Clique com o botao direito no arquivo ZIP e escolha "Extrair tudo".
echo 3. Abra a pasta extraida e clique duas vezes em instalar.bat.
echo.
pause
exit /b 1
