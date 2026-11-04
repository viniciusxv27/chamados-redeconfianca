#!/bin/sh
# Instala o agente em um computador Linux da loja: sudo sh instalar.sh
set -e
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO=/opt/agente-cameras
mkdir -p "$DESTINO"
cp "$ORIGEM"/* "$DESTINO"/
chmod 600 "$DESTINO/config.json"
python3 -m venv "$DESTINO/venv"
"$DESTINO/venv/bin/pip" install --quiet -r "$DESTINO/requirements.txt"
cat > /etc/systemd/system/agente-cameras.service <<SERVICO
[Unit]
Description=Agente de cameras
After=network-online.target
[Service]
ExecStart=$DESTINO/venv/bin/python $DESTINO/agente.py $DESTINO/config.json
WorkingDirectory=$DESTINO
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
SERVICO
systemctl daemon-reload
systemctl enable --now agente-cameras
echo "Agente rodando. Instalando a analise (pode demorar)..."
if "$DESTINO/venv/bin/pip" install --quiet -r "$DESTINO/requirements-ia.txt"; then
    systemctl restart agente-cameras
fi
echo "Pronto."
