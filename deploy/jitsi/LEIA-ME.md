# Jitsi próprio no Easypanel — salas de /reunioes/ sem limite de usuários

**Por quê:** a conta gratuita do 8x8 (JaaS) tem limite de usuários ativos por mês. Quando estoura, a sala de
`/reunioes/{id}/sala/` mostra *"you've reached your monthly active users limit"*. O Jitsi é gratuito e de código
aberto: rodando no servidor de vocês, não tem limite de usuários nem de tempo. O portal assina a entrada de cada
pessoa, então só entra quem vem pelo portal ou pelo link de visitante.

Arquivo pronto para colar: [`docker-compose.easypanel.yml`](docker-compose.easypanel.yml) (imagens oficiais
`jitsi/*:stable-11031`, de junho/2026).

---

## Antes de começar (5 minutos)

1. **Escolha o endereço.** Exemplo usado aqui: `video.redeconfianca.com.br`.
2. **DNS:** crie um registro **A** `video` → **IP público da VPS do Easypanel**. Espere propagar.
3. **IP público da VPS:** anote. Vai em `JVB_ADVERTISE_IPS`.
4. **Gere três senhas aleatórias** (no terminal do seu Mac, um comando por senha):

   ```bash
   openssl rand -hex 32
   ```

   Uma para `JWT_APP_SECRET`, outra para `JICOFO_AUTH_PASSWORD` e outra para `JVB_AUTH_PASSWORD`.
   Guarde a do `JWT_APP_SECRET`: ela também vai no portal.

5. **Porta 10000/udp liberada** no firewall da VPS. Na Hostinger: *VPS → Firewall → Adicionar regra* →
   protocolo **UDP**, porta **10000**, origem **qualquer**. Sem essa porta a chamada conecta, mas ninguém se vê
   nem se ouve.

> Requisito da máquina: para algumas reuniões simultâneas com cerca de 10 pessoas, **4 vCPU e 8 GB de RAM**.
> O que mais pesa é a banda de internet da VPS. Se a VPS do Easypanel já estiver cheia (portal, painéis), prefira
> uma VPS só para o vídeo.

---

## Passo 1 — Criar o serviço no Easypanel

1. No Easypanel, abra o projeto (ou crie um: **+ Create Project** → `video`).
2. **+ Service → Compose**. Nome: `jitsi`.
3. Em **Source**, escolha **Create** e cole o conteúdo inteiro de
   [`docker-compose.easypanel.yml`](docker-compose.easypanel.yml). Salve.

## Passo 2 — Variáveis (aba **Environment** do serviço `jitsi`)

Cole, trocando os valores entre `< >`:

```ini
PUBLIC_URL=https://video.redeconfianca.com.br
JVB_ADVERTISE_IPS=<IP público da VPS>
JWT_APP_ID=redeconfianca
JWT_APP_SECRET=<senha 1>
JICOFO_AUTH_PASSWORD=<senha 2>
JVB_AUTH_PASSWORD=<senha 3>
```

Salve.

## Passo 3 — Domínio (aba **Domains**)

1. **+ Add Domain**.
2. Host: `video.redeconfianca.com.br` · **HTTPS ligado** · Path `/`.
3. Destino: serviço **`web`**, porta **`80`**.
4. Salve. O Easypanel tira o certificado sozinho.

## Passo 4 — Subir

Clique em **Deploy**. Na primeira vez ele baixa as quatro imagens (alguns minutos). Quando os quatro serviços
(`web`, `prosody`, `jicofo`, `jvb`) estiverem **verdes**, abra `https://video.redeconfianca.com.br`:
a página do Jitsi tem de abrir com cadeado. Se você tentar entrar numa sala direto por ali, ele **recusa**
(pede token): é o esperado — a entrada é só pelo portal.

---

## Passo 5 — Conectar no portal

1. Entre no portal como SUPERADMIN e abra **`/reunioes/configuracao/`**.
2. No bloco **"Caminho 1 — servidor de vídeo próprio"**:
   - **Endereço do servidor:** `video.redeconfianca.com.br` (sem `https://`, sem barra no fim).
   - **AppID do servidor:** `redeconfianca` (o mesmo `JWT_APP_ID`).
   - **Segredo:** a **senha 1** (o mesmo `JWT_APP_SECRET`).
3. **Salvar**. A tela passa a mostrar, em verde:
   *"Salas abrindo em video.redeconfianca.com.br, sem limite de usuários. A entrada é assinada pelo portal."*

As credenciais do 8x8 podem continuar guardadas: com servidor próprio no endereço, o portal não usa mais o 8x8.
Para voltar ao 8x8 a qualquer momento, basta pôr `meet.jit.si` no endereço e salvar.

## Passo 6 — Testar (10 minutos)

1. Crie uma reunião de teste em `/reunioes/` e abra a sala. Você entra direto, com o seu nome do cadastro.
2. Abra a mesma sala com **outra pessoa em outra rede** (um celular no 4G, fora do Wi-Fi da empresa).
   Os dois têm de se ver e se ouvir.
3. Gere o **link de visitante** da reunião e abra numa janela anônima: o visitante entra com o nome que digitar.
4. Confira se o **fundo de tela e a marca** da sala aparecem. Se não aparecerem, o vídeo funciona igual — me
   avise que eu ajusto pelo lado do servidor.

---

## Se algo não funcionar

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| A sala abre, mas ninguém se vê/ouve, ou a chamada cai após ~30 s | Porta **10000/udp** fechada ou `JVB_ADVERTISE_IPS` errado | Libere a UDP 10000 no firewall da VPS; confira o IP público em `JVB_ADVERTISE_IPS` e faça **Deploy** de novo |
| "Você não está autorizado a entrar" | Segredo ou AppID do portal diferente do `.env` | Cole de novo o mesmo `JWT_APP_SECRET` e `JWT_APP_ID` no portal e no Easypanel |
| Página do Jitsi não abre / sem cadeado | DNS ainda não propagou ou domínio apontando para porta errada | Confira o registro A e, em **Domains**, serviço `web` porta `80` |
| A sala fica em branco dentro do portal | O navegador bloqueou o Jitsi dentro do portal | Abra o console do navegador e me mande o erro |
| Funciona no Wi-Fi da empresa e falha no 4G (ou o contrário) | Rede bloqueando UDP | Me avise: dá para ligar um servidor TURN no mesmo Easypanel |

## Observações técnicas

- O token leva `sub` = `meet.jitsi` (o `XMPP_DOMAIN` padrão das imagens). Se um dia mudar o `XMPP_DOMAIN`,
  ajuste `DOMINIO_XMPP_PROPRIO` em `reunioes/jaas.py`.
- Com entrada por token, todo mundo que entra pelo portal pode moderar a sala (silenciar, remover) — inclusive
  visitantes. Se quiser só o organizador como moderador, dá para restringir depois.
- Para atualizar o Jitsi no futuro: troque `stable-11031` pela versão nova nas quatro imagens e clique em **Deploy**.
