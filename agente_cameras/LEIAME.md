# Agente de câmeras da loja

Roda em um computador ligado o tempo todo na mesma rede do gravador, com Windows 10 ou 11 de 64 bits. Só faz chamadas de saída para o portal: não abre porta no roteador nem muda nada no gravador.

## Instalar

1. Em `/cameras/configuracao/`, clique em **Baixar instalador** na linha da loja.
2. Leve o arquivo para o computador da loja e clique duas vezes. Aceite o pedido de administrador.

É um arquivo só, com tudo dentro. O código da loja está no nome do arquivo: não renomeie. Ele vale por 7 dias.

O Windows pode avisar que protegeu o computador, porque o instalador não tem assinatura digital: clique em "Mais informações" e "Executar assim mesmo".

O agente acha o gravador na rede, testa o usuário e a senha do cadastro, descobre o caminho do vídeo e quantas câmeras existem. O andamento e os erros aparecem na linha da loja. Fica em `C:\ProgramData\AgenteCameras`, com o registro em `agente.log`.

Em `/cameras/loja/<id>/`, abra cada câmera e marque as áreas: **Mostruário**, **Porta** (arraste de fora para dentro), **Cadeira do cliente** e **Frente da tela** do matinal. A câmera da porta analisa 3 imagens por segundo; as outras, 1.

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

O modelo é o YOLOX tiny (Apache 2.0). O construtor baixa o Python oficial e o modelo, confere o SHA-256 e empacota as bibliotecas do Windows.
