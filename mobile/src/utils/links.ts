/**
 * Links que chegam do portal (notificações, bloqueios): o que o app sabe abrir
 * vira tela nativa; o resto abre o portal num navegador dentro do app.
 */
import { type Href, router } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';

import { URL_PORTAL } from '@/services/api';

/** URL do portal (o que vem nas notificações) → tela do módulo no app. Ordem importa: o específico antes. */
const ROTAS_DE_MODULO: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^\/impulso\/metas\/(\d+)\/?$/, (m) => `/modulo/impulso/${m[1]}`],
  [/^\/impulso\/metas\/?$/, () => '/modulo/impulso/metas'],
  [/^\/impulso\/(acompanhamento\/?)?$/, () => '/modulo/impulso'], // Conectar, Inovar etc. seguem no portal
  [/^\/ponto\/solicitacoes\/(\d+)\/?/, (m) => `/modulo/ponto/${m[1]}`],
  [/^\/ponto\/dp\/?$/, () => '/modulo/ponto/caixa'],
  [/^\/ponto\/solicitacoes\/?$/, () => '/modulo/ponto/solicitacoes'],
  [/^\/ponto\/?$/, () => '/modulo/ponto'], // "Meu ponto" do Tangerino mora em /ponto/
  [/^\/contracheque\/informes\/(\d+)\/?$/, (m) => `/modulo/contracheque/informe/${m[1]}`],
  [/^\/contracheque\/(\d+)\/?$/, (m) => `/modulo/contracheque/${m[1]}`],
  [/^\/contracheque\/?$/, () => '/modulo/contracheque'],
  [/^\/documentos\/(\d+)\/?$/, (m) => `/modulo/documentos/${m[1]}`],
  [/^\/documentos\/?$/, () => '/modulo/documentos'],
  [/^\/checklists\/view\/(\d+)\/?$/, (m) => `/modulo/checklists/${m[1]}`],
  [/^\/checklists\/?$/, () => '/modulo/checklists'],
  [/^\/cameras\/alertas\/(\d+)\/?$/, (m) => `/modulo/cameras/alerta/${m[1]}`],
  [/^\/quadro-hc\/?$/, () => '/modulo/quadro_hc'],
  [/^\/cursos\/?$/, () => '/modulo/cursos'],
  [/^\/prizes\/?$/, () => '/modulo/mercadinho'],
  [/^\/facilitador\/?$/, () => '/modulo/facilitador'],
  [/^\/power-bi\/metas\/?$/, () => '/modulo/power_bi_metas'],
  [/^\/power-bi\/?$/, () => '/modulo/power_bi'],
  [/^\/agenda\/transcricoes\/(\d+)\/?$/, (m) => `/modulo/agenda/transcricao/${m[1]}`],
  [/^\/agenda\/transcricoes\/?$/, () => '/modulo/agenda/transcricoes'],
  [/^\/agenda\/solicitacoes\/?$/, () => '/modulo/agenda/convites'],
  [/^\/agenda\/?$/, () => '/modulo/agenda'],
];

export function abrirNoPortal(caminho: string) {
  const url = /^https?:\/\//.test(caminho) ? caminho : `${URL_PORTAL}${caminho.startsWith('/') ? '' : '/'}${caminho}`;
  return WebBrowser.openBrowserAsync(url, { presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET });
}

/** Abre o link de uma notificação. Devolve true se virou tela do app. */
export function abrirLink(link: string | null) {
  if (!link) return false;
  let caminho = link;
  try {
    const url = new URL(link, URL_PORTAL);
    if (url.origin === new URL(URL_PORTAL).origin) caminho = url.pathname;
  } catch {
    // link estranho: tenta como veio
  }
  const chamado = caminho.match(/^\/tickets\/(\d+)\/?/);
  if (chamado) {
    router.push({ pathname: '/chamado/[id]', params: { id: chamado[1] } });
    return true;
  }
  const comunicado = caminho.match(/^\/communications\/(\d+)\/?/);
  if (comunicado) {
    router.push({ pathname: '/comunicado/[id]', params: { id: comunicado[1] } });
    return true;
  }
  // Módulos: a mesma URL da web → a tela do app. Sem acesso, a própria tela mostra o 403 do servidor.
  for (const [padrao, destino] of ROTAS_DE_MODULO) {
    const achou = caminho.match(padrao);
    if (achou) {
      router.push(destino(achou) as Href);
      return true;
    }
  }
  void abrirNoPortal(link);
  return false;
}
