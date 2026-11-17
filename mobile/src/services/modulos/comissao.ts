/** Módulo Comissão: minha comissão do mês, pilar a pilar, o histórico e as médias (/users/commission/ do portal). */
import { api } from '../api';

/** A planilha vem do OneDrive e as metas por pilar levam alguns segundos na primeira leitura do mês. */
const TEMPO_PLANILHA = 60_000;

export type PapelComissao = 'superadmin' | 'coordenador' | 'gerente' | 'recepcionista' | 'cn' | 'aparte';

export type ReferenciaComissao = { ano: number; mes: number; fase: 'antes' | 'pos'; rotulo: string; fase_rotulo: string };

export type PessoaComissao = { id: number; nome: string; setor: string | null; papel: string };

export type PilarComissao = {
  chave: string;
  nome: string;
  icone: string;
  cor: string;
  /** Do mês mais antigo para o de referência, em % (103,85 = 103,85%). */
  atingimentos: { mes: string; pct: number }[];
  media: number;
  /** Atingimento da loja no pilar, em %; null quando a planilha não traz. */
  carteira: number | null;
  carteira_sem_dado: boolean;
  hunter_id: string | null;
  meta_total: number;
  pago: number;
  exclusao: number;
  comissao: number;
  premiacao_individual: number;
  premiacao_coletiva: number;
  hunter: number;
  iq_pct: number | null;
  total: number;
};

export type DadosPlanilha = {
  tipo: 'planilha';
  nome: string;
  cargo: string;
  pdv: string;
  coordenador: string;
  coordenador_rotulo: string;
  total: number;
  comissao_total: number;
  premiacao_total: number;
  total_pilares: number;
  aceleradores: { seis_pilares: number; campanha: number };
  descontos: { advertencia: number; excedente: number; price: number; total: number };
  composicao: { rotulo: string; valor: number }[];
  pilares: PilarComissao[];
};

export type DadosAparte = {
  tipo: 'aparte';
  total: number;
  salario_base: number;
  tem_configuracao: boolean;
  pilares: { chave: string; nome: string; meta: number; realizado: number; quantidade: boolean; atingimento: number; fator: number; comissao: number }[];
};

export type ResumoComissao = {
  papel: PapelComissao;
  papel_rotulo: string;
  pode_ver_outros: boolean;
  pode_projecao: boolean;
  liberado: boolean;
  aviso: string | null;
  referencia: (ReferenciaComissao & { selecao_invalida: boolean }) | null;
  opcoes: ReferenciaComissao[];
  alvo: PessoaComissao | null;
  vendo_outro: boolean;
  precisa_escolher: boolean;
  dados: DadosPlanilha | DadosAparte | null;
  erro: string | null;
};

export type FiltroComissao = { ano?: number; mes?: number; fase?: string; usuario?: number };

export type PartesHistorico = { comissao: number; premiacao: number; bonus_loja: number; aceleradores: number; ajuste_iq: number; descontos: number; recebido: number };

export type HistoricoComissao = {
  escopo: 'eu' | 'equipe';
  tem_equipe: boolean;
  partes: { chave: keyof PartesHistorico; rotulo: string }[];
  total: PartesHistorico;
  media_mes: number;
  melhor_mes: { rotulo: string; recebido: number } | null;
  n_pessoas: number;
  meses: {
    chave: string;
    rotulo: string;
    total: PartesHistorico;
    previa: boolean;
    rascunho: boolean;
    pct: number;
    pessoas: number;
    linhas: { pessoa: string; cargo: string; loja: string; fase: string; recebido: number; partes: Omit<PartesHistorico, 'recebido'> }[];
  }[];
  pessoas: { pessoa: string; cargo: string; loja: string; total: number; meses_com_valor: number; media: number }[];
};

export type ResumoMedia = { quantos: number; total: number; media: number; maior: number; menor: number };

export type ProjecaoComissao = {
  status: 'pronto' | 'calculando';
  desatualizado: boolean;
  gerado_em: string | null;
  referencia: string;
  resumo: ResumoMedia;
  referencia_loja: ResumoMedia | null;
  papeis: { chave: string; rotulo: string; quantos: number }[];
  linhas: { id: number; nome: string; papel: string; papel_rotulo: string; setor: string | null; coordenador: string | null; ganho: number; tem_dado: boolean }[];
};

export const comissao = {
  resumo: (filtro: FiltroComissao) => api.get<ResumoComissao>('/m/comissao/', { params: filtro, timeout: TEMPO_PLANILHA }).then((r) => r.data),
  pessoas: (filtro: FiltroComissao) =>
    api.get<{ resultados: PessoaComissao[] }>('/m/comissao/pessoas/', { params: filtro, timeout: TEMPO_PLANILHA }).then((r) => r.data.resultados),
  historico: (escopo: 'eu' | 'equipe') => api.get<HistoricoComissao>('/m/comissao/historico/', { params: { escopo } }).then((r) => r.data),
  projecao: () => api.get<ProjecaoComissao>('/m/comissao/projecao/').then((r) => r.data),
};
