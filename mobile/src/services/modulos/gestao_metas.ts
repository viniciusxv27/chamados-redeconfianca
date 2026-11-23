/** Gestão de metas (/gestao-metas/ do portal): a meta do mês de cada loja, pilar a pilar. */
import { api } from '../api';

const BASE = '/m/gestao_metas';

export type CompetenciaResumo = {
  codigo: string;
  rotulo: string;
  rotulo_curto: string;
  fechada: boolean;
  status_nome: string;
  metas: number | null;
  observacao: string | null;
};

export type InicioGestaoMetas = {
  papel: 'SUPERADMIN' | 'EDITOR' | 'VISUALIZADOR';
  papel_rotulo: string;
  pode_editar: boolean;
  lojas_ativas: number;
  competencias: CompetenciaResumo[];
  url_web: string;
};

export type Decisao = 'CIMA' | 'BAIXO';

/** Uma loja (ou subtotal: RJ, ES, SP, REDE) num pilar. Variações são frações (0,1 = +10%). */
export type LinhaMeta = {
  meses: (number | null)[];
  media_quad: number | null;
  real_ano_anterior: number | null;
  meta_vivo: number | null;
  meta_anterior: number | null;
  meta: number | null;
  /** O que vale como meta do mês: a meta (R$) ou, na Fixa, a nova meta (quantidade). */
  alvo: number | null;
  mxm: number | null;
  yxy: number | null;
  mxquad: number | null;
  mxmeta: number | null;
  // Fixa
  meses_valor?: (number | null)[];
  media_quad_valor?: number | null;
  ticket?: number | null;
  ticket_da_rede?: boolean;
  qtd_cns?: number | null;
  qtd?: number | null;
  cima?: number | null;
  baixo?: number | null;
  decisao?: Decisao;
  nova_meta?: number | null;
  // Loja
  loja_id?: number;
  loja?: string;
  regiao?: string | null;
  divisao?: { nome: string; percentual: number; meta: number | null }[];
  // Subtotal
  titulo?: string;
  qtd_lojas?: number;
};

export type Competencia = CompetenciaResumo & {
  anterior: string | null;
  seguinte: string | null;
  pilar: string;
  pilar_nome: string;
  fixa: boolean;
  pilares: { valor: string; rotulo: string }[];
  rotulos: { meses: string[]; ano_anterior: string; mes_anterior: string; competencia: string };
  meses_faltando: string[];
  anterior_ausente: boolean;
  anterior_aberta: boolean;
  pode_editar: boolean;
  pode_fechar: boolean;
  pode_reabrir: boolean;
  origens: { valor: string; rotulo: string }[];
  linhas: LinhaMeta[];
  grupos: LinhaMeta[];
  rede: LinhaMeta;
  /** Planilha .xlsx (link assinado, vale 5 min). */
  excel: string;
  url_web: string;
};

export type RealizadoAgora = {
  situacao: 'banco' | 'gravado' | 'futuro' | 'sem_dados';
  /** pilar → loja_id → valores. */
  lojas: Record<string, Record<string, { valor: number; tv: number; fixa: number; qtd: number | null }>>;
  atualizado_em: string | null;
  erro: string;
};

export type CampoMeta = 'meta' | 'qtd_cns' | 'decisao';

export type Divisao = { total: number; consultores: number; base: number; cheios: number; itens: { nome: string; percentual: number; meta: number }[] };

export type ItemHistorico = {
  id: number;
  loja: string | null;
  pilar: string | null;
  pilar_nome: string | null;
  campo: string;
  antes: string | null;
  depois: string | null;
  origem: string | null;
  por: string | null;
  quando: string;
};

export const gestaoMetas = {
  inicio: () => api.get<InicioGestaoMetas>(`${BASE}/`).then((r) => r.data),
  competencia: (codigo: string, pilar: string) => api.get<Competencia>(`${BASE}/${codigo}/`, { params: { pilar } }).then((r) => r.data),
  /** Busca no banco de vendas: pode demorar na 1ª vez (depois fica 10 min em cache no portal). */
  realizadoAgora: (codigo: string, atualizar = false) =>
    api.get<RealizadoAgora>(`${BASE}/${codigo}/realizado-agora/`, { params: atualizar ? { atualizar: 1 } : undefined, timeout: 90_000 }).then((r) => r.data),
  salvar: (codigo: string, corpo: { loja: number; pilar: string; campo: CampoMeta; valor: string }) =>
    api.post<{ ok: boolean; linha: LinhaMeta | null; salvo_em: string | null }>(`${BASE}/${codigo}/salvar/`, corpo).then((r) => r.data),
  dividir: (codigo: string, corpo: { loja: number; pilar: string; campos: Partial<Record<CampoMeta, string>> }) =>
    api
      .post<{ ok: boolean; linha: LinhaMeta | null; salvo_em: string | null; divisao: Divisao; mensagem: string }>(`${BASE}/${codigo}/dividir/`, corpo, { timeout: 60_000 })
      .then((r) => r.data),
  preencher: (codigo: string, corpo: { pilar: string; origem: string; percentual: string; arredondar: string; somente_vazias: boolean }) =>
    api.post<{ ok: boolean; mensagem: string }>(`${BASE}/${codigo}/preencher/`, corpo, { timeout: 60_000 }).then((r) => r.data),
  fechar: (codigo: string, acao: 'fechar' | 'reabrir') => api.post<{ ok: boolean; mensagem: string }>(`${BASE}/${codigo}/fechar/`, { acao }).then((r) => r.data),
  historico: (codigo: string, pilar: string) => api.get<{ resultados: ItemHistorico[] }>(`${BASE}/${codigo}/historico/`, { params: { pilar: pilar || undefined } }).then((r) => r.data.resultados),
};
