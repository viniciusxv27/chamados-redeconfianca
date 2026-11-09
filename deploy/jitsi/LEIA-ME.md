# Servidor de vídeo próprio (Jitsi) — sem limite de usuários

**Por quê:** a conta gratuita do 8x8 (JaaS) tem limite de usuários ativos por mês. Quando ele estoura, a sala de
`/reunioes/{id}/sala/` mostra *"you've reached your monthly active users limit"*. O servidor público `meet.jit.si`
não é saída: exige um moderador logado e, embutido em outro site, derruba a chamada em 5 minutos.

O Jitsi é gratuito e de código aberto. Instalado num servidor da empresa, não tem limite de usuários nem de tempo.
O portal assina a entrada de cada pessoa (JWT), então só entra quem vem pelo portal ou pelo link de visitante.

## 1. Servidor

- Uma VPS Linux (Ubuntu 22.04/24.04) com Docker e Docker Compose. Para algumas reuniões simultâneas com cerca de
  10 pessoas: **4 vCPU e 8 GB de RAM**. O que mais pesa é a banda de internet do servidor.
- Um endereço, por exemplo `video.redeconfianca.com.br`, com registro DNS **A** apontando para o IP da VPS.
- Firewall liberado: **80/tcp**, **443/tcp** e **10000/udp** (o áudio e o vídeo passam pela 10000/udp).

## 2. Instalação (Jitsi oficial em Docker)

```bash
git clone https://github.com/jitsi/docker-jitsi-meet && cd docker-jitsi-meet
cp env.example .env
./gen-passwords.sh
mkdir -p ~/.jitsi-meet-cfg/{web,transcripts,prosody/config,prosody/prosody-plugins-custom,jicofo,jvb,jigasi,jibri}
```

No arquivo `.env`, ajuste (troque pelos seus valores):

```ini
PUBLIC_URL=https://video.redeconfianca.com.br
HTTP_PORT=80
HTTPS_PORT=443
ENABLE_LETSENCRYPT=1
LETSENCRYPT_DOMAIN=video.redeconfianca.com.br
LETSENCRYPT_EMAIL=ti@redeconfianca.com.br
JVB_ADVERTISE_IPS=<IP público da VPS>

# Entrada assinada pelo portal
ENABLE_AUTH=1
AUTH_TYPE=jwt
JWT_APP_ID=redeconfianca
JWT_APP_SECRET=<o mesmo segredo colado no portal>
JWT_ACCEPTED_ISSUERS=redeconfianca
JWT_ACCEPTED_AUDIENCES=jitsi
ENABLE_GUESTS=0
```

Suba:

```bash
docker compose up -d
```

Confira abrindo `https://video.redeconfianca.com.br` no navegador: a página do Jitsi abre e uma sala pede token.

## 3. Portal

Em `/reunioes/configuracao/` (SUPERADMIN), no "Caminho 1 — servidor de vídeo próprio":

1. **Endereço do servidor:** `video.redeconfianca.com.br` (sem `https://`).
2. **AppID:** o mesmo `JWT_APP_ID` do `.env` (ex.: `redeconfianca`).
3. **Segredo:** o mesmo `JWT_APP_SECRET` do `.env`. A tela sugere um segredo aleatório; use o mesmo nos dois lugares.
4. Salve. A tela passa a dizer *"Salas abrindo em video.redeconfianca.com.br, sem limite de usuários"*.

As credenciais do 8x8 podem ficar guardadas: com um servidor próprio no endereço, o portal não usa mais o 8x8.
Para voltar ao 8x8, basta pôr `meet.jit.si` no endereço.

## Observações

- O token leva `sub` = `meet.jitsi` (o `XMPP_DOMAIN` padrão do docker-jitsi-meet). Se mudar o `XMPP_DOMAIN`,
  avise para ajustar `DOMINIO_XMPP_PROPRIO` em `reunioes/jaas.py`.
- O fundo de tela e a identidade visual da sala (branding) continuam vindo do portal, como no 8x8.
- Depois de subir, faça uma chamada de teste com 3 pessoas em redes diferentes (uma no 4G). Se o vídeo não
  aparecer para quem está fora, quase sempre é a porta **10000/udp** fechada ou o `JVB_ADVERTISE_IPS` errado.
