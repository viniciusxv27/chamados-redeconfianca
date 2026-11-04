# Instala o agente de câmeras: Python, dependências e a tarefa que roda ao ligar o computador.
# Cada passo aparece na tela, vai para instalacao.log e chega ao portal, na linha da loja.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersao = '3.12.10'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersao/python-$PythonVersao-amd64.exe"
$Origem = Split-Path -Parent $MyInvocation.MyCommand.Path
$Destino = Join-Path $env:ProgramData 'AgenteCameras'
$Tarefa = 'Agente de Cameras'
$Config = $null

function Avisar-Portal([string] $Texto) {
    # Só avisa: sem internet aqui, o erro de verdade aparece no próximo passo.
    if (-not $Config) { return }
    try {
        $Corpo = [Text.Encoding]::UTF8.GetBytes('erro=' + [uri]::EscapeDataString("Instalador: $Texto"))
        Invoke-WebRequest -UseBasicParsing -Method Post -TimeoutSec 15 -Uri ($Config.portal.TrimEnd('/') + '/cameras/agente/descoberta/') `
            -Headers @{ Authorization = "Bearer $($Config.token)" } -ContentType 'application/x-www-form-urlencoded' -Body $Corpo | Out-Null
    } catch { }
}

function Passo([string] $Texto) {
    Write-Host ''
    Write-Host ">> $Texto" -ForegroundColor Cyan
    Avisar-Portal $Texto
}

function Rodar([string] $Programa, [string[]] $Argumentos) {
    # Aviso em stderr (o pip manda vários) não é falha: quem decide é o código de saída.
    $Antes = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Programa @Argumentos 2>&1 | ForEach-Object { Write-Host $_ }
        $Codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $Antes
    }
    if ($Codigo -ne 0) { throw "$(Split-Path -Leaf $Programa) terminou com o erro $Codigo." }
}

function Python-Serve([string] $Caminho) {
    if (-not $Caminho -or -not (Test-Path $Caminho) -or $Caminho -like '*\WindowsApps\*') { return $false }
    $Antes = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Caminho -c 'import struct,sys; sys.exit(0 if sys.version_info >= (3, 9) and struct.calcsize("P") == 8 else 1)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $Antes
    }
}

function Achar-Python {
    # O da pasta do agente primeiro; depois um que já esteja no computador.
    $Proprio = Join-Path $Destino 'python\python.exe'
    if (Python-Serve $Proprio) { return $Proprio }
    foreach ($Comando in @('python', 'python3')) {
        foreach ($Achado in @(Get-Command $Comando -All -ErrorAction SilentlyContinue)) {
            if (Python-Serve $Achado.Source) { return $Achado.Source }
        }
    }
    foreach ($Pasta in @("$env:ProgramFiles\Python312", "$env:ProgramFiles\Python311", "$env:ProgramFiles\Python310")) {
        $Caminho = Join-Path $Pasta 'python.exe'
        if (Python-Serve $Caminho) { return $Caminho }
    }
    return $null
}

function Instalar-Python {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Este computador é de 32 bits. O agente precisa de Windows de 64 bits.' }
    $Arquivo = Join-Path $env:TEMP "python-$PythonVersao-amd64.exe"
    Write-Host "Baixando de $PythonUrl (27 MB)..."
    Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $Arquivo -TimeoutSec 600
    Unblock-File -Path $Arquivo
    $Assinatura = Get-AuthenticodeSignature -FilePath $Arquivo
    if ($Assinatura.Status -ne 'Valid' -or $Assinatura.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
        Remove-Item -Force $Arquivo
        throw 'O arquivo baixado do Python não tem a assinatura oficial. Instalação cancelada.'
    }
    $Alvo = Join-Path $Destino 'python'
    $Processo = Start-Process -FilePath $Arquivo -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=1', "TargetDir=`"$Alvo`"", 'PrependPath=0', 'Include_launcher=0',
        'Include_test=0', 'Include_doc=0', 'Shortcuts=0', 'AssociateFiles=0')
    Remove-Item -Force -ErrorAction SilentlyContinue $Arquivo
    if ($Processo.ExitCode -ne 0) { Write-Host "O instalador do Python terminou com o código $($Processo.ExitCode)." -ForegroundColor Yellow }
}

function Registrar-Tarefa([string] $PythonDoAgente) {
    $Acao = New-ScheduledTaskAction -Execute $PythonDoAgente -Argument "`"$Destino\agente.py`" `"$Destino\config.json`"" -WorkingDirectory $Destino
    $Quando = New-ScheduledTaskTrigger -AtStartup
    $Quem = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
    # Sem limite de tempo: o padrão do Windows mata a tarefa depois de 3 dias.
    $Regras = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $Tarefa -Action $Acao -Trigger $Quando -Principal $Quem -Settings $Regras -Force | Out-Null
}

try {
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
    Start-Transcript -Path (Join-Path $Destino 'instalacao.log') -Force | Out-Null
    $Config = Get-Content -Raw -Encoding UTF8 (Join-Path $Origem 'config.json') | ConvertFrom-Json

    Passo 'Copiando os arquivos'
    Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
    Get-ChildItem -Path $Origem -File | Copy-Item -Destination $Destino -Force
    Get-ChildItem -Path $Destino -File | Unblock-File
    # Quem só usa este computador não precisa ler o token da loja. Por SID: o nome do grupo muda com o idioma.
    & icacls (Join-Path $Destino 'config.json') /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null

    Passo 'Procurando o Python'
    $Python = Achar-Python
    if (-not $Python) {
        Passo 'Instalando o Python (pode levar alguns minutos)'
        Instalar-Python
        $Python = Achar-Python
    }
    if (-not $Python) { throw 'Não foi possível instalar o Python neste computador.' }
    Write-Host "Usando $Python"

    Passo 'Preparando o agente (pode levar alguns minutos)'
    $Venv = Join-Path $Destino 'venv'
    $PythonDoAgente = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Python-Serve $PythonDoAgente)) { Rodar $Python @('-m', 'venv', '--clear', $Venv) }
    Rodar $PythonDoAgente @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', (Join-Path $Destino 'requirements.txt'))

    Passo 'Ligando o agente'
    Registrar-Tarefa $PythonDoAgente
    Start-ScheduledTask -TaskName $Tarefa
    Start-Sleep -Seconds 20
    $Estado = (Get-ScheduledTask -TaskName $Tarefa).State
    if ($Estado -ne 'Running') { throw "O agente não ficou rodando (situação: $Estado). Veja agente.log em $Destino." }
    Write-Host 'Agente rodando. As imagens devem aparecer no portal em alguns minutos.' -ForegroundColor Green

    # Daqui em diante, falha não desfaz nada: as imagens seguem sem a análise.
    Write-Host ''
    Write-Host '>> Instalando a análise de imagem (é grande, pode levar bastante tempo). Não feche esta janela.' -ForegroundColor Cyan
    try {
        Rodar $PythonDoAgente @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', (Join-Path $Destino 'requirements-ia.txt'))
        Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $Tarefa
        Write-Host 'Pronto: agente rodando com análise.' -ForegroundColor Green
    } catch {
        Write-Host "A análise não foi instalada ($($_.Exception.Message)). As imagens continuam funcionando." -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host 'INSTALAÇÃO CONCLUÍDA.' -ForegroundColor Green
} catch {
    $Motivo = $_.Exception.Message
    Write-Host ''
    Write-Host 'A INSTALAÇÃO NÃO TERMINOU.' -ForegroundColor Red
    Write-Host $Motivo -ForegroundColor Red
    Write-Host "Detalhes em $Destino\instalacao.log"
    Avisar-Portal "falhou: $Motivo"
} finally {
    try { Stop-Transcript | Out-Null } catch { }
}
