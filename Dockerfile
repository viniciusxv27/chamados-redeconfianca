FROM python:3.11-slim

# Definir diretório de trabalho
WORKDIR /app

# Variáveis de ambiente para Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y \
    postgresql-client \
    build-essential \
    libpq-dev \
    curl \
    ffmpeg \
    libffi-dev \
    libssl-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements primeiro (para cache do Docker)
COPY requirements.txt .

# Instalar dependências Python
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Navegador do assistente (BIs do /power-bi/ e links): Chromium headless (só o
# "headless shell") + as bibliotecas de sistema que ele pede. Soma ~350 MB à imagem.
# Com um navegador remoto (NAVEGADOR_CDP_URL, ex.: browserless), dá para pular:
#   docker build --build-arg INSTALAR_NAVEGADOR=0 .
ARG INSTALAR_NAVEGADOR=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$INSTALAR_NAVEGADOR" = "1" ]; then \
        python -m playwright install --with-deps --only-shell chromium \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Copiar código da aplicação
COPY . .

# Instalador da Ponte da Automação SAP (o app da loja dentro do portal, em /sap/automacao/):
# baixa o Python oficial do Windows (conferido por SHA-256) e empacota com o NSIS.
# Sai em ponte_sap/dist/PonteAutomacaoSAP.exe e o portal oferece esse arquivo às lojas.
# Se falhar (ex.: build sem internet), a imagem segue sem ele. Para pular: --build-arg CONSTRUIR_PONTE=0
ARG CONSTRUIR_PONTE=1
RUN if [ "$CONSTRUIR_PONTE" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends nsis && rm -rf /var/lib/apt/lists/* \
        && python ponte_sap/construir_instalador.py --saida /app/ponte_sap/dist/PonteAutomacaoSAP.exe --cache /tmp/ponte-cache \
        && rm -rf /tmp/ponte-cache \
        || echo "AVISO: instalador da Ponte não gerado; o portal segue sem ele"; \
    fi

# Criar diretórios necessários e definir permissões
RUN mkdir -p staticfiles media logs && \
    chmod +x deploy_new.sh

# Criar um arquivo placeholder para staticfiles
RUN touch staticfiles/.keep

# Expor porta
EXPOSE 8000

# Script de saúde
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Comando padrão
CMD ["./deploy_new.sh"]
