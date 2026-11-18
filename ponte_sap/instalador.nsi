; Ponte da Automação SAP: um arquivo só, na conta da pessoa (sem administrador). Gerado por construir_instalador.py.
Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user
ManifestDPIAware true
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
LoadLanguageFile "${NSISDIR}\Contrib\Language files\PortugueseBR.nlf"

!ifndef VERSAO
  !define VERSAO "0.0.0"
!endif
!define CHAVE_REMOVER "Software\Microsoft\Windows\CurrentVersion\Uninstall\PonteAutomacaoSAP"

Name "Ponte da Automação SAP"
Caption "Ponte da Automação SAP ${VERSAO} - Rede Confiança"
OutFile "${SAIDA}"
ShowInstDetails show
BrandingText "Rede Confiança"
Page instfiles
UninstPage instfiles

VIProductVersion "${VERSAO}.0"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "ProductName" "Ponte da Automação SAP"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "CompanyName" "Rede Confiança"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "FileDescription" "Mostra a Automação SAP dentro do portal"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "FileVersion" "${VERSAO}"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "ProductVersion" "${VERSAO}"
VIAddVersionKey /LANG=${LANG_PORTUGUESEBR} "LegalCopyright" "Rede Confiança"

Var Codigo
Var Mensagem

!macro PararPonte
    nsExec::Exec 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-Process python* -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like \"$INSTDIR\*\" } | Stop-Process -Force -ErrorAction SilentlyContinue"'
    Pop $0
    Sleep 800
!macroend

Section
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONSTOP "A ponte precisa de Windows 10 ou 11 de 64 bits." /SD IDOK
        Abort
    ${EndIf}
    ${IfNot} ${AtLeastWin10}
        MessageBox MB_ICONSTOP "A ponte precisa de Windows 10 ou 11." /SD IDOK
        Abort
    ${EndIf}
    SetShellVarContext current
    StrCpy $INSTDIR "$LOCALAPPDATA\PonteAutomacaoSAP\ponte"
    !insertmacro PararPonte
    RMDir /r "$INSTDIR\runtime"
    SetOutPath "$INSTDIR"
    SetOverwrite on
    File /r "${PACOTE}\*"
    Delete "$INSTDIR\resultado.txt"
    WriteUninstaller "$INSTDIR\Desinstalar.exe"
    WriteRegStr HKCU "${CHAVE_REMOVER}" "DisplayName" "Ponte da Automação SAP (Rede Confiança)"
    WriteRegStr HKCU "${CHAVE_REMOVER}" "DisplayVersion" "${VERSAO}"
    WriteRegStr HKCU "${CHAVE_REMOVER}" "Publisher" "Rede Confiança"
    WriteRegStr HKCU "${CHAVE_REMOVER}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
    WriteRegDWORD HKCU "${CHAVE_REMOVER}" "NoModify" 1
    WriteRegDWORD HKCU "${CHAVE_REMOVER}" "NoRepair" 1

    DetailPrint "Configurando"
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -u "$INSTDIR\instalar.py"'
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
        MessageBox MB_ICONSTOP "$Mensagem" /SD IDOK
        SetErrorLevel 2
    ${EndIf}
SectionEnd

Section "Uninstall"
    SetShellVarContext current
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -u "$INSTDIR\instalar.py" --remover'
    Pop $0
    !insertmacro PararPonte
    RMDir /r "$INSTDIR"
    DeleteRegKey HKCU "${CHAVE_REMOVER}"
SectionEnd
