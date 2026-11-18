/** Módulo Metas: as metas do mês por pilar, CN e loja (/power-bi/metas/ do portal). */
import { api } from '../api';

export type PerfilMetas = 'cn' | 'gerente' | 'rede';

export type Competencia = { ano: number; mes: number; rotulo: string; fixa_quantidade: boolean; atualizado_em: string | null };

/** Um pilar no painel: total em R$ (ou em quantidade, se for a FIXA da carga "FIXA em quantidade"). */
export type LinhaPilar = { pilar: string; total: number; quantidade: boolean; barra: number };

export type Ranking = { titulo: string; itens: { nome: string; total: number }[] };

export type FiltroMetas = { ano?: number; mes?: number; loja?: string; pilar?: string; vendedor?: string; cn?: string };

export type ResumoMetas = {
  perfil: PerfilMetas;
  pode_gerenciar: boolean;
  competencia: Competencia | null;
  /** Pediu um mês sem carga: veio a mais recente. */
  competencia_trocada: boolean;
  competencias: Competencia[];
  filtros: { lojas: string[]; pilares: string[]; vendedores: string[]; cns: string[]; loja: string; pilar: string; vendedor: string; cn: string };
  fixa_quantidade: boolean;
  /** `meta` é null para o gerente (a web não mostra o card "Meta total" para ele). */
  totais: { meta: number | null; cn: number; loja: number };
  contagens: { cn: number; loja: number };
  painel_cn: LinhaPilar[];
  tabela_gerente: { colunas: string[]; linhas: { vendedor: string; celulas: { valor: number; quantidade: boolean }[]; total: number }[] } | null;
  top_consultores: Ranking | null;
  painel_loja: LinhaPilar[];
  top_lojas: Ranking;
};

export type CargaMetas = Competencia & { id: number; arquivo: string; itens: number; enviado_por: string | null; criado_em: string | null };

export const metas = {
  resumo: (filtro: FiltroMetas) => api.get<ResumoMetas>('/m/power_bi_metas/', { params: filtro }).then((r) => r.data),
  cargas: () => api.get<{ resultados: CargaMetas[] }>('/m/power_bi_metas/cargas/').then((r) => r.data.resultados),
};
