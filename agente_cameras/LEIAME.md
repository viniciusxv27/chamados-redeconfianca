# Agente de câmeras da loja

Roda em um computador ligado o tempo todo na mesma rede do gravador. Só faz chamadas de saída para o portal: não abre porta no roteador nem muda nada no gravador.

1. Em `/cameras/configuracao/`, clique em **Baixar instalador** na linha da loja.
2. No computador da loja, **extraia o ZIP** (botão direito, "Extrair tudo") e clique duas vezes em `instalar.bat`. Aceite o pedido de administrador. No Linux: `sudo sh instalar.sh`.

Não precisa instalar Python antes: se faltar, o instalador baixa a versão oficial de python.org e confere a assinatura. Se o Windows avisar que protegeu o computador, clique em "Mais informações" e "Executar assim mesmo". A janela só fecha quando você mandar, e o andamento aparece também na linha da loja, no portal. Registro em `C:\ProgramData\AgenteCameras\instalacao.log` e `agente.log`.

O agente acha o gravador na rede, testa o usuário e a senha do cadastro, descobre o caminho do vídeo e quantas câmeras existem, e manda tudo para o portal. Se algo impedir, o motivo aparece na linha da loja.

Precisa de: RTSP ligado no gravador e internet no computador. A análise usa uma imagem por segundo por câmera; sem placa de vídeo, conte com um i5 ou melhor para até 8 câmeras e meça na primeira loja. Se a parte de análise não instalar, as imagens continuam funcionando.

Em `/cameras/loja/<id>/`, abra cada câmera e marque o mostruário arrastando sobre a imagem. Sem área marcada, só vale o alerta de pessoa com a loja fechada.

Para as medições, marque também: **Porta** (arraste de fora para dentro), **Cadeira do cliente** em cada mesa e **Frente da tela** do matinal. A câmera da porta analisa 3 imagens por segundo; as outras, 1.

`config.json` guarda o token da loja: não coloque em pasta compartilhada. Baixar o instalador de novo troca o token, e o agente antigo para.
