; Um arquivo só: clique duas vezes, aceite o administrador e pronto. Gerado por construir_instalador.py.
Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel admin
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
LoadLanguageFile "${NSISDIR}\Contrib\Language files\PortugueseBR.nlf"

Name "Agente de Câmeras"
Caption "Agente de Câmeras - Rede Confiança"
OutFile "${SAIDA}"
ShowInstDetails show
AutoCloseWindow false
BrandingText "Rede Confiança"
Page instfiles

Var Codigo
Var Mensagem

Section
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONSTOP "Este computador é de 32 bits. O agente precisa de Windows 10 ou 11 de 64 bits."
        Abort
    ${EndIf}
    ${IfNot} ${AtLeastWin10}
        MessageBox MB_ICONSTOP "O agente precisa de Windows 10 ou 11."
        Abort
    ${EndIf}

    SetShellVarContext all
    StrCpy $INSTDIR "$APPDATA\AgenteCameras"

    ; O agente antigo segura os arquivos abertos: para antes de copiar.
    DetailPrint "Parando o agente anterior, se houver"
    nsExec::Exec 'schtasks /End /TN "Agente de Cameras"'
    Pop $0
    Sleep 2000
    RMDir /r "$INSTDIR\venv"
    RMDir /r "$INSTDIR\python"

    DetailPrint "Copiando os arquivos"
    SetOutPath "$INSTDIR"
    SetOverwrite on
    File /r "${PACOTE}\*"
    Delete "$INSTDIR\resultado.txt"

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
        MessageBox MB_ICONINFORMATION "$Mensagem"
    ${Else}
        DetailPrint "$Mensagem"
        MessageBox MB_ICONSTOP "$Mensagem"
    ${EndIf}
SectionEnd
