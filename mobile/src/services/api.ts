/**
 * Cliente HTTP da API do portal (/api/mobile/v1/).
 *
 * - Token por aparelho no cabeçalho `Authorization: Bearer …`.
 * - Erros viram `ErroApi` com mensagem em português pronta para a tela.
 * - 401 (sessão encerrada) e 423 (bloqueio do portal: férias, jornada, curso,
 *   trilha) avisam quem estiver ouvindo — a sessão reage trocando de tela.
 */
import { AxiosError, create } from 'axios';

import type { Bloqueio } from './tipos';

export const URL_PORTAL = (process.env.EXPO_PUBLIC_API_URL || 'https://chamados.redeconfianca.com.br').replace(/\/+$/, '');

export const api = create({
  baseURL: `${URL_PORTAL}/api/mobile/v1`,
  timeout: 20000,
  headers: { Accept: 'application/json' },
});

let tokenAtual: string | null = null;

export function definirToken(token: string | null) {
  tokenAtual = token;
}

api.interceptors.request.use((config) => {
  if (tokenAtual) config.headers.Authorization = `Bearer ${tokenAtual}`;
  return config;
});

export class ErroApi extends Error {
  status: number;
  campos: Record<string, string>;
  bloqueio: Bloqueio | null;
  semConexao: boolean;

  constructor(mensagem: string, opcoes: Partial<Pick<ErroApi, 'status' | 'campos' | 'bloqueio' | 'semConexao'>> = {}) {
    super(mensagem);
    this.name = 'ErroApi';
    this.status = opcoes.status ?? 0;
    this.campos = opcoes.campos ?? {};
    this.bloqueio = opcoes.bloqueio ?? null;
    this.semConexao = opcoes.semConexao ?? false;
  }
}

type Ouvintes = { sessaoEncerrada: Set<() => void>; bloqueio: Set<(b: Bloqueio) => void> };
const ouvintes: Ouvintes = { sessaoEncerrada: new Set(), bloqueio: new Set() };

export function aoEncerrarSessao(fn: () => void) {
  ouvintes.sessaoEncerrada.add(fn);
  return () => {
    ouvintes.sessaoEncerrada.delete(fn);
  };
}

export function aoBloquear(fn: (b: Bloqueio) => void) {
  ouvintes.bloqueio.add(fn);
  return () => {
    ouvintes.bloqueio.delete(fn);
  };
}

api.interceptors.response.use(
  (resposta) => resposta,
  (erro: AxiosError<{ erro?: string; detail?: string | { erro?: string; bloqueio?: Bloqueio }; campos?: Record<string, string>; bloqueio?: Bloqueio }>) => {
    if (!erro.response) {
      const tempo = erro.code === 'ECONNABORTED';
      return Promise.reject(
        new ErroApi(tempo ? 'O portal demorou para responder. Tente de novo.' : 'Sem conexão com o portal. Confira a internet.', {
          semConexao: true,
        }),
      );
    }
    const { status, data } = erro.response;
    const detalhe = typeof data?.detail === 'object' ? data.detail : undefined;
    const bloqueio = data?.bloqueio ?? detalhe?.bloqueio ?? null;
    const mensagem =
      data?.erro ?? detalhe?.erro ?? (typeof data?.detail === 'string' ? data.detail : undefined) ?? mensagemPadrao(status);

    if (status === 401 && tokenAtual) ouvintes.sessaoEncerrada.forEach((fn) => fn());
    if (status === 423 && bloqueio) ouvintes.bloqueio.forEach((fn) => fn(bloqueio));

    return Promise.reject(new ErroApi(mensagem, { status, campos: data?.campos, bloqueio }));
  },
);

function mensagemPadrao(status: number) {
  if (status === 401) return 'Sua sessão terminou. Entre de novo.';
  if (status === 403) return 'Você não tem permissão para isso.';
  if (status === 404) return 'Não encontramos o que você procurou.';
  if (status === 429) return 'Muitas tentativas. Aguarde um minuto.';
  if (status >= 500) return 'O portal teve um problema. Tente de novo em instantes.';
  return 'Algo deu errado. Tente de novo.';
}

/** Mensagem amigável de qualquer erro (ErroApi ou não). */
export function mensagemDoErro(erro: unknown) {
  if (erro instanceof ErroApi) return erro.message;
  return 'Algo deu errado. Tente de novo.';
}
