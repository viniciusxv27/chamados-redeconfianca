# Agente de câmeras da loja

Roda em um computador ligado na mesma rede do gravador, e só faz chamadas de saída para o portal.

1. No portal, em `/cameras/configuracao/`, preencha o IP do gravador e o número de câmeras, e gere o token da loja.
2. No gravador, confirme que o RTSP está habilitado e que o caminho do portal funciona (teste com VLC).
3. No computador da loja:

   ```bash
   python -m venv venv
   venv/bin/pip install -r requirements.txt
   cp config.exemplo.json config.json
   venv/bin/python agente.py config.json
   ```

4. Deixe o agente iniciar junto com o computador. `config.json` guarda o token: não coloque em pasta compartilhada.

A análise usa uma imagem por segundo por câmera. Sem placa de vídeo, conte com um computador i5 ou melhor para até 8 câmeras; meça na primeira loja.
Com `"analisar": false`, o agente só manda as imagens.

Em `/cameras/loja/<id>/`, abra cada câmera e marque o mostruário arrastando sobre a imagem. Sem área marcada, só vale o alerta de pessoa com a loja fechada.
