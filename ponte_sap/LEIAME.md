# Ponte da Automação SAP

Mostra o AutomacaoSAP **dentro do portal** (Visão SAP › Automação SAP). O app continua rodando no
Windows da loja, porque comanda o SAP GUI daquele computador. A ponte é só o caminho:

- abre o app (ou acha o que já está aberto) e tira as janelas dele da área de trabalho;
- manda para a página só os pedaços da tela que mudaram (faixas PNG);
- entrega o mouse e o teclado da página direto às janelas do app, sem mexer no mouse de verdade
  nem tirar o foco do navegador;
- as perguntas do app ("Gravar no SAP?") aparecem por cima, dentro do portal;
- fechou a página (ou a ponte caiu)? Em ~15 s o app volta para a área de trabalho.

Só escuta em `127.0.0.1:47861` e só conversa com a página do portal (origem conferida pelo
navegador). Cada sessão é conferida no portal (`/sap/automacao/api/ponte/conferir/`): fica no
log quem abriu e em qual computador. Sem bibliotecas de fora: só a biblioteca padrão do Python.

## Instalar na loja

Na própria página: "Baixar a Ponte" → abrir o arquivo. Fica em `%LOCALAPPDATA%\PonteAutomacaoSAP`,
abre com o Windows, não pede administrador. Se o AutomacaoSAP ainda não estiver no computador, a
página oferece instalar (baixa a versão publicada no portal, mantendo `config` e `logs`). Uma
instalação antiga em `C:\AutomacaoSAP` é achada e usada.

Na primeira vez o Chrome/Edge pode perguntar se o portal pode acessar outros apps do dispositivo:
**Permitir**.

## Gerar uma versão nova

Em qualquer sistema com `makensis` (Mac: `brew install nsis`):

```bash
venv/bin/python ponte_sap/construir_instalador.py
```

Depois, em `/sap/automacao/`, o SUPERADMIN publica o `.exe` em "Ponte (instalador)".

## Testar

`teste_ponte_sap.py` (Windows, qualquer Python com tkinter): sobe a ponte numa porta de teste com um
app Tk no lugar do AutomacaoSAP e faz clique, digitação com acento, Enter, roda, lista, perguntas
Sim/Não, redimensionar, outra aba, queda da ponte e instalação. `teste_automacao_sap.py` cobre o portal.
