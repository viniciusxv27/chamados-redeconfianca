# Instala o agente de cameras: copia os arquivos, prepara o Python e deixa rodando ao ligar o computador.
$ErrorActionPreference = 'Stop'
$Origem = Split-Path -Parent $MyInvocation.MyCommand.Path
$Destino = Join-Path $env:ProgramData 'AgenteCameras'
$Tarefa = 'Agente de Cameras'
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
Copy-Item -Force (Join-Path $Origem '*') $Destino
# Quem so usa este computador nao precisa ler o token da loja.
icacls (Join-Path $Destino 'config.json') /inheritance:r /grant:r 'SYSTEM:F' 'Administradores:F' | Out-Null

$Python = Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Python) {
    Write-Host 'Instalando o Python...'
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $Python = Get-Command py, python | Select-Object -First 1
}
Set-Location $Destino
& $Python.Source -m venv venv
& .\venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

$Comando = "`"$Destino\venv\Scripts\python.exe`" `"$Destino\agente.py`" `"$Destino\config.json`""
schtasks /Create /F /TN $Tarefa /SC ONSTART /RU SYSTEM /RL HIGHEST /TR $Comando | Out-Null
schtasks /Run /TN $Tarefa | Out-Null
Write-Host 'Agente rodando: as imagens desta loja ja devem aparecer no portal. Agora instalando a analise (pode demorar).'
try {
    & .\venv\Scripts\python.exe -m pip install --quiet -r requirements-ia.txt
    schtasks /End /TN $Tarefa | Out-Null
    schtasks /Run /TN $Tarefa | Out-Null
    Write-Host 'Pronto, com analise.'
} catch {
    Write-Host 'A analise nao foi instalada; as imagens continuam funcionando.'
}
Read-Host 'Pode fechar esta janela (Enter)'
