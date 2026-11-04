@echo off
rem Clique duas vezes. Pede permissao de administrador e instala o agente de cameras desta loja.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0instalar.ps1\"'"
