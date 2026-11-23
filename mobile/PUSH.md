# Notificações push do app

Tudo o que cai no sino do portal (os dois sinos: `notifications.UserNotification` e o antigo
`core.Notification`) também sai como push nos celulares em que a pessoa entrou pelo app.

- **Servidor:** `api_mobile/push.py` manda pelo Expo Push Service
  (`https://exp.host/--/api/v2/push/send`) depois do commit, numa fila com uma thread
  por processo, em lotes de 100. Respeita as preferências da pessoa (push ou sino desligado,
  horário silencioso, tipo desligado). Aparelho que o Expo diz não existir mais
  (`DeviceNotRegistered`) é apagado. Se der erro, só vai para o log.
- **Aparelho:** `api_mobile.PushMobile`, preso à sessão do app (`TokenMobile`). Sair do app,
  revogar a sessão ou ela expirar (60 dias parada) corta o push.
- **App:** `src/services/push.ts` (permissão, canal do Android, token, registro) e
  `src/components/navegacao/push-do-portal.tsx` (explicação antes do pedido, toque que abre a
  tela certa, também com o app fechado).

## O que falta (uma vez só)

### 1. Banco: criar a tabela

```bash
venv/bin/python manage.py migrate api_mobile   # 0002_pushmobile: só cria a tabela api_mobile_pushmobile
```

É uma tabela nova, então o código que já está nos outros servidores não quebra. Antes deste
migrate o código novo também roda: o login funciona e só o push fica parado (com um aviso no
log). Rode sozinho, sem nenhum `teste_*.py` aberto.

### 2. Firebase: `google-services.json` (o Android só recebe push pelo FCM)

1. Entre em <https://console.firebase.google.com> → **Adicionar projeto** (ex.: "Rede Confiança").
   O Google Analytics pode ficar desligado.
2. No projeto: **Adicionar app → Android**.
   - Nome do pacote: **`com.redeconfianca.portal`** (tem que ser igual ao `android.package`
     do `app.json`).
   - Apelido: "Rede Confiança". O SHA-1 não é necessário para push.
3. Baixe o **`google-services.json`** e salve em **`mobile/google-services.json`**
   (do lado do `app.json`). Pule os passos de "adicionar SDK" — o Expo faz isso.
4. Versione esse arquivo (`git add mobile/google-services.json`): o `eas build` só envia o
   que está no git (e o que não está no `.gitignore`), e o `app.json` aponta para ele em
   `android.googleServicesFile`. Ele identifica o app, não é a chave secreta.

> Enquanto o arquivo não existir, o `eas build` para Android falha ao copiar
> `./google-services.json`. Se precisar gerar um build antes de criar o Firebase, tire a linha
> `"googleServicesFile"` do `app.json` — o app funciona, só não registra push.

### 3. Chave da conta de serviço FCM v1 → EAS (para o Expo conseguir mandar pelo FCM)

1. No Firebase: **⚙ Configurações do projeto → Contas de serviço → Gerar nova chave
   privada**. Baixa um JSON (`...-firebase-adminsdk-....json`).
   **Esse é secreto:** não coloque no git (o `.gitignore` do `mobile/` já ignora
   `*firebase-adminsdk*.json` e `fcm-*.json`).
2. Envie para o EAS, de dentro de `mobile/`:

   ```bash
   npx eas-cli@latest credentials
   ```

   - Platform: **Android**
   - Profile: **production** (as credenciais de push valem para o pacote, servem para todos os perfis)
   - **Google Service Account**
   - **Manage your Google Service Account Key for Push Notifications (FCM V1)**
   - **Set up a Google Service Account Key for Push Notifications (FCM V1)**
   - **Upload a new service account key** → escolha o JSON do passo 1.

   (O mesmo pode ser feito no site: expo.dev → projeto → **Credentials → Android →
   com.redeconfianca.portal → FCM V1 service account key → Add a service account key**.)
3. Guarde o JSON num lugar seguro fora do projeto (ou apague: dá para gerar outro).

### 4. Gerar o build

Push remoto **não funciona no Expo Go do Android** (desde o SDK 53): teste com o APK do perfil
`preview` ou com o build de produção. Não precisa de nada no servidor além do passo 1.

### 5. iOS (quando for publicar)

O `eas build` para iOS oferece criar a chave de push da Apple (APNs) sozinho; ou
`npx eas-cli@latest credentials` → iOS → **Push Notifications: Manage your Apple Push
Notifications Key**. No Expo Go do iPhone o push já funciona para teste.

## Como conferir

1. Instale o build, entre no app e toque em **Ativar notificações** na folha que aparece
   (no Android 12 ou mais antigo não há pergunta: já vem liberado).
2. No admin do Django: **API do app mobile → Push dos aparelhos** deve ter uma linha da sua sessão.
3. Gere qualquer aviso no portal para você (ex.: comente num chamado seu) — chega o push;
   tocar abre a tela do app (chamado, comunicado, módulo) ou o portal no navegador do app.
4. Para testar só o aparelho: copie o token da linha do admin e use
   <https://expo.dev/notifications>.

Se não chegar: veja o log do portal (`push mobile: ...`). `DeviceNotRegistered` = o app foi
desinstalado ou o token mudou (o registro some sozinho; abrir o app de novo registra outra
vez). `InvalidCredentials` = falta o passo 3. Em desenvolvimento, o app escreve no console
`[push] aparelho sem push: ...` quando não consegue o token (ex.: falta o `google-services.json`).

### Opcional: segurança extra do Expo

Se ligar **Enhanced security for push notifications** no painel do Expo, crie um access token
em expo.dev e coloque `EXPO_ACCESS_TOKEN=...` no `.env` do servidor (o `api_mobile/push.py`
manda no cabeçalho `Authorization`). Sem isso ligado, não precisa.
