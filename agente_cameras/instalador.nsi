; Um arquivo só: clique duas vezes, aceite o administrador e pronto. Gerado por construir_instalador.py.
; Também roda calado (/S): é assim que o agente 3.1+ se atualiza sozinho, numa tarefa à parte do Windows.
Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
RequestExecutionLevel admin
ManifestDPIAware true
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "FileFunc.nsh"
LoadLanguageFile "${NSISDIR}\Contrib\Language files\PortugueseBR.nlf"

!ifndef VERSAO
  !define VERSAO "0.0"
!endif
!define TAREFA "Agente de Cameras"
!define CHAVE_REMOVER "Software\Microsoft\Windows\CurrentVersion\Uninstall\AgenteCameras"
!define ESPACO_MINIMO_MB 1500

Name "Agente de Câmeras"
Caption "Agente de Câmeras ${VERSAO} - Rede Confiança"
OutFile "${SAIDA}"
ShowInstDetails show
ShowUninstDetails show
AutoCloseWindow false
BrandingText "Rede Confiança"
Page instfiles
UninstPage instfiles

VIProductVersion "${VERSAO}.0.0"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "ProductName" "Agente de Câmeras"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "CompanyName" "Rede Confiança"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "FileDescription" "Instalador do agente de câmeras das lojas"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "FileVersion" "${VERSAO}"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "ProductVersion" "${VERSAO}"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "LegalCopyright" "Rede Confiança"

Var Codigo
Var Mensagem

; Para o agente que estiver rodando desta pasta (pela tarefa ou não) e espera soltar os arquivos.
!macro PararAgente
    DetailPrint "Parando o agente anterior, se houver"
    nsExec::Exec 'schtasks /End /TN "${TAREFA}"'
    Pop $0
    nsExec::Exec 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-Process python* -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like \"$INSTDIR\*\" } | Stop-Process -Force -ErrorAction SilentlyContinue"'
    Pop $0
    Sleep 1500
!macroend

Section
    SetRegView 64
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONSTOP "Este computador é de 32 bits. O agente precisa de Windows 10 ou 11 de 64 bits." /SD IDOK
        Abort
    ${EndIf}
    ${IfNot} ${AtLeastWin10}
        MessageBox MB_ICONSTOP "O agente precisa de Windows 10 ou 11." /SD IDOK
        Abort
    ${EndIf}

    SetShellVarContext all
    StrCpy $INSTDIR "$APPDATA\AgenteCameras"

    ; Espaço para o programa, os vídeos dos alertas que esperam internet (até 3 GB) e o registro.
    ${GetRoot} "$APPDATA" $1
    ${DriveSpace} "$1\" "/D=F /S=M" $2
    ${If} $2 < ${ESPACO_MINIMO_MB}
        MessageBox MB_ICONSTOP "Pouco espaço livre no disco $1 ($2 MB). Libere pelo menos ${ESPACO_MINIMO_MB} MB e rode de novo." /SD IDOK
        Abort
    ${EndIf}

    !insertmacro PararAgente
    ; O runtime sai inteiro: biblioteca de versão antiga misturada com a nova quebra a análise.
    ; config.json, filas de medições e vídeos ficam: a loja não perde nada ao atualizar.
    RMDir /r "$INSTDIR\runtime"
    RMDir /r "$INSTDIR\venv"
    RMDir /r "$INSTDIR\python"
    ${If} ${FileExists} "$INSTDIR\runtime\*.*"
        ; Algum arquivo ainda preso: mais uma tentativa depois de parar de novo.
        !insertmacro PararAgente
        RMDir /r "$INSTDIR\runtime"
    ${EndIf}

    DetailPrint "Copiando os arquivos"
    SetOutPath "$INSTDIR"
    SetOverwrite on
    File /r "${PACOTE}\*"
    Delete "$INSTDIR\resultado.txt"
    WriteUninstaller "$INSTDIR\Desinstalar.exe"

    ; Aparece em "Adicionar ou remover programas", com versão e remoção completa.
    WriteRegStr HKLM "${CHAVE_REMOVER}" "DisplayName" "Agente de Câmeras (Rede Confiança)"
    WriteRegStr HKLM "${CHAVE_REMOVER}" "DisplayVersion" "${VERSAO}"
    WriteRegStr HKLM "${CHAVE_REMOVER}" "Publisher" "Rede Confiança"
    WriteRegStr HKLM "${CHAVE_REMOVER}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "${CHAVE_REMOVER}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
    WriteRegStr HKLM "${CHAVE_REMOVER}" "QuietUninstallString" '"$INSTDIR\Desinstalar.exe" /S'
    WriteRegDWORD HKLM "${CHAVE_REMOVER}" "NoModify" 1
    WriteRegDWORD HKLM "${CHAVE_REMOVER}" "NoRepair" 1
    ${GetSize} "$INSTDIR" "/S=0K" $3 $4 $5
    IntFmt $3 "0x%08X" $3
    WriteRegDWORD HKLM "${CHAVE_REMOVER}" "EstimatedSize" "$3"

    DetailPrint "Configurando esta loja"
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -u "$INSTDIR\instalar.py" "$EXEFILE"'
    Pop $Codigo

    StrCpy $Mensagem "A instalação não terminou. Tente de novo e, se repetir, avise quem cuida do portal."
    IfFileExists "$INSTDIR\resultado.txt" 0 mostrar
    FileOpen $0 "$INSTDIR\resultado.txt" r
    FileReadUTF16LE $0 $Mensagem 900
    FileClose $0

    mostrar:
    ${If} $Codigo == "0"
        MessageBox MB_ICONINFORMATION "$Mensagem" /SD IDOK
    ${Else}
        DetailPrint "$Mensagem"
        MessageBox MB_ICONSTOP "$Mensagem" /SD IDOK
        SetErrorLevel 2
    ${EndIf}
SectionEnd

Section "Uninstall"
    SetRegView 64
    SetShellVarContext all
    ; Tira as tarefas e a regra do firewall, e avisa o portal (a linha da loja mostra que foi removido).
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -u "$INSTDIR\instalar.py" --remover'
    Pop $0
    !insertmacro PararAgente
    nsExec::Exec 'schtasks /Delete /TN "${TAREFA}" /F'
    Pop $0
    RMDir /r "$INSTDIR"
    DeleteRegKey HKLM "${CHAVE_REMOVER}"
SectionEnd
