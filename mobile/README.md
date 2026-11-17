# App Rede Confiança (Android e iOS)

App nativo do portal, feito em **Expo SDK 58** (React Native 0.88). Consome o próprio portal Django pela API
`/api/mobile/v1/` (app `api_mobile` na raiz do repositório). As regras de permissão continuam no Django.

## Rodar

```bash
cd mobile
cp .env.example .env        # aponte EXPO_PUBLIC_API_URL para o portal
npm install
npx expo start              # QR code no Expo Go, "a" Android, "i" iOS, "w" navegador
```

No celular, `localhost` é o próprio celular: use o IP da máquina na rede (ex.: `http://192.168.0.10:8009`).
A API precisa estar no ar nesse endereço — em produção só depois do deploy do `api_mobile` + `migrate`.

Antes de entregar: `npx tsc --noEmit` e `npx expo lint`. Build de loja: `npx eas-cli@latest build` (EAS).

## Estrutura

```
src/
  app/                    rotas (Expo Router — cada arquivo é uma tela)
    _layout.tsx           provedores + quem vê o quê (Stack.Protected pela sessão)
    entrar.tsx            login
    bloqueado.tsx         férias / jornada / curso / trilha / cadastro (mesmas travas do portal)
    (abas)/               barra inferior: Início, Chamados, Mural, Avisos, Perfil
    chamado/[id].tsx      detalhe + conversa      chamado/novo.tsx   abrir chamado (modal)
    comunicado/[id].tsx   comunicado + "Estou ciente"
  components/ui/          design system (Texto, Botao, Campo, Cartao, Selo, Segmentado, Seletor, Esqueleto…)
  components/{chamados,comunicados,navegacao}/   peças de cada assunto
  constants/tokens.js     cores, fontes, raios — fonte única (o tailwind.config.js lê daqui)
  constants/theme.ts      tipografia, sombras, movimento
  providers/              sessão (token), tema (sistema/claro/escuro), cache (TanStack Query)
  services/               api.ts (axios + erros em português), portal.ts (endpoints), tipos.ts
  hooks/consultas.ts      hooks de dados das telas
  utils/                  datas relativas, cores de status, links do portal
```

## Decisões

- **Identidade visual do portal:** laranja `#FF6B35`, preto do menu lateral, Montserrat, paleta do tema escuro
  do portal (`static/css/tema-escuro.css`). Mude cores em `src/constants/tokens.js`.
- **NativeWind v4** (estável) para layout/espaçamento por `className`. A **cor do texto e do fundo vem do tema por
  `style`**: no react-native-web, trocar a classe deixava a antiga grudada.
- **Animações** com Reanimated 4 (entradas em cascata, molas no toque, pílula da aba, coração do curtir, campo que
  acende no foco). Com o React Compiler ligado, use `sv.get()`/`sv.set()` em vez de `sv.value`.
- **Sessão:** token por aparelho no cofre do sistema (`expo-secure-store`); o portal guarda só o hash
  (`api_mobile.TokenMobile`, revogável no admin). 401 → volta ao login; 423 → tela de bloqueio.
- **Comunicados** chegam em HTML; a API manda o texto limpo (parágrafos e listas) — o app não usa WebView.
