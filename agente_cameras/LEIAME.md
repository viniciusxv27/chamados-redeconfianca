# Agente de câmeras da loja

Roda em um computador ligado o tempo todo na mesma rede do gravador, com Windows 10 ou 11 de 64 bits. Só faz chamadas de saída para o portal: não abre porta no roteador nem muda nada no gravador.

## Instalar

1. Em `/cameras/configuracao/`, clique em **Baixar instalador** na linha da loja.
2. Leve o arquivo para o computador da loja e clique duas vezes. Aceite o pedido de administrador.

É um arquivo só, com tudo dentro. O código da loja está no nome do arquivo: não renomeie. Ele vale por 7 dias.

O Windows pode avisar que protegeu o computador, porque o instalador não tem assinatura digital: clique em "Mais informações" e "Executar assim mesmo".

O agente acha o gravador na rede, testa o usuário e a senha do cadastro, descobre o caminho do vídeo e quantas câmeras existem. O andamento e os erros aparecem na linha da loja. Fica em `C:\ProgramData\AgenteCameras`, com o registro em `agente.log`.

O instalador:
- confere Windows 10/11 de 64 bits e 1,5 GB livres;
- para o agente antigo e troca o runtime inteiro (config, medições e vídeos na fila ficam);
- cria a tarefa "Agente de Cameras", que liga no boot e tem uma vigia de 5 em 5 minutos que religa o agente se ele cair;
- libera o vídeo ao vivo no Firewall;
- só termina depois de o agente conversar com o portal (`contato.txt`). Se não conversar em 1 minuto, avisa sem falhar.

Também avisa se o relógio do Windows estiver muito errado. Aparece em "Adicionar ou remover programas" com a versão. Remover tira as tarefas, a regra do firewall e a pasta, e avisa o portal.

## Atualização automática (agente 3.1 em diante)

Em `/cameras/configuracao/`, bloco "Versão do agente", liga-se a atualização automática (nasce desligada). Com ela ligada, o sinal das lojas com versão mais velha que a publicada traz o link, o tamanho e o SHA-256. O agente baixa, confere e roda o instalador calado (`/S`) numa tarefa à parte do Windows ("Agente de Cameras - atualizar"). Ela é necessária porque o instalador para a tarefa do agente. Falhou? Tenta a mesma versão de novo só depois de 6 h. Agentes 2.x não sabem se atualizar: reinstale uma vez à mão.

## Saúde e robustez

A cada minuto o sinal leva a saúde do computador:
- câmeras com imagem, quadros por segundo e reconexões;
- detector em uso;
- filas de medições e vídeos;
- disco, processador e memória;
- diferença do relógio.

O portal mostra isso na linha da loja e no painel. As horas das medições usam o relógio do portal, não o do computador. Vídeos e medições sobem numa thread própria, para um vídeo grande não deixar a loja "sem contato". O laço principal e cada câmera nunca morrem com erro: registram e tentam de novo. Câmera cuja thread caiu é recriada. Com o portal fora, o agente espera cada vez mais (até 2 min), com um sorteio, para as lojas não voltarem todas juntas. Enquanto o agente roda, o Windows não suspende o computador.

Antes de abrir e depois de fechar (tolerância por loja, 60 min por padrão), gente na loja não vira alerta de "loja fechada". Cada loja escolhe também se quer o alerta de permanência, que nasce desligado.

Em `/cameras/loja/<id>/`, abra cada câmera e marque as áreas: **Mostruário**, **Porta** (arraste de fora para dentro), **Cadeira do cliente** e **Frente da tela** do matinal. A câmera da porta analisa 3 imagens por segundo; as outras, 1.

## Alertas: vídeo de 3 minutos e IA

Cada câmera guarda em memória os últimos ~2 minutos de vídeo (pedaços de 10 s em MP4, copiando os pacotes do gravador, sem recomprimir). Quando a análise dispara um alerta, o agente manda a foto na hora e, 1min30 depois, o MP4 com 1min30 antes e 1min30 depois do acontecimento e uns 10 quadros-chave concentrados no momento da ação. Os vídeos esperam em `fila_clipes` se a internet cair (até 3 dias ou 3 GB). Câmera em H.265 é convertida para H.264 só nesse trecho.

No portal, uma IA de visão olha os quadros-chave com o que o detector mediu e com as decisões anteriores daquela câmera, e devolve risco, linha do tempo e o que conferir no vídeo. Cada "furto confirmado" ou "falso alarme" (com o motivo) ensina: o portal ajusta o rigor de cada câmera e tipo de alerta e manda ao agente no próximo contato (`ajustes` do sinal).

A análise usa o YOLOX-s em 640 px quando o computador aguenta (com placa de vídeo, pelo DirectML, quase sempre) e o YOLOX-tiny quando não. O `agente.log` diz qual ficou e quantos ms leva cada imagem.

## Vídeo ao vivo

Vai direto do computador da loja para o navegador (WebRTC), criptografado e sem passar pelo servidor do portal. As miniaturas usam a qualidade menor do gravador; ao abrir uma câmera, vem a principal, sem recompressão. Câmera em H.265 é convertida para H.264, o que gasta bem mais processador. Em redes muito fechadas o vídeo pode não conectar: a tela avisa e segue com imagens. A tentativa fica registrada em `SessaoAoVivo`.

## Testar

`teste_cameras.py` cobre o portal. `teste_cameras_ao_vivo.py` passa por RTSP e WebRTC de verdade na própria máquina (instruções no começo do arquivo).

## Gerar uma versão nova

Em qualquer sistema com `makensis` (Mac: `brew install nsis`):

```bash
venv/bin/python agente_cameras/construir_instalador.py
venv/bin/python manage.py publicar_instalador_cameras agente_cameras/dist/AgenteCameras.exe
```

A versão vem de `VERSAO` em `agente.py` e vai para as propriedades do .exe. A publicação guarda versão, tamanho e SHA-256, que a atualização automática usa. Publique só depois do deploy do portal com o mesmo código.

Os modelos são o YOLOX-s e o YOLOX-tiny (Apache 2.0). O construtor baixa o Python oficial e os modelos, confere o SHA-256 e empacota as bibliotecas do Windows (com o ONNX Runtime DirectML, para usar a placa de vídeo).
