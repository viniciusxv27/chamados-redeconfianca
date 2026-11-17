/** Módulo Simulador: metas, realizado, projeção e prêmio estimado por pilar (/simulator/ do portal). */
import { api } from '../api';

/** A primeira consulta do mês busca o realizado no MySQL (até ~30 s); depois fica em cache por 15 min. */
const TEMPO_MOTOR = 90_000;

export type Visao = 'projecao' | 'realizado';

export type PessoaSimulador = { id: number; nome: string; setor: string | null; papel: string };

export type Comparativo = { meta: number | null; realizado: number | null; atingimento: number | null };

export type PilarSimulador = {
  chave: string;
  rotulo: string;
  /** Fixa conta quantidade (não reais). */
  quantidade: boolean;
  meta: number | null;
  realizado: number | null;
  receita: number | null;
  /** Fração: 0,88 = 88%. */
  atingimento: number | null;
  /** Quanto falta por dia útil para bater 100% (0 = já bateu). */
  por_dia: number | null;
  taxa_comissao: number | null;
  comissao: number | null;
  taxa_premio: number | null;
  premio: number | null;
  premio_pdv: number | null;
  hunter2: number | null;
  hunter3: number | null;
  /** O que o pilar põe no bolso. */
  ganho: number | null;
  loja: Comparativo | null;
  coordenacao: Comparativo | null;
};

export type Simulacao = {
  nome: string;
  pdv: string | null;
  coordenador: string | null;
  papel: string;
  papel_rotulo: string;
  ganho_total: number | null;
  totais: { pilares: number | null; hunter2: number | null; hunter3: number | null; bonus_6_7: number | null; acelerador: number | null; sem_acelerador: number | null };
  acelerador_ligado: boolean;
  acelerador_pct: number;
  sniper: boolean;
  sniper_de: string | null;
  gerente_operacional: boolean;
  percentual_operacional: number | null;
  gerente_base: string | null;
  salario_base: number | null;
  pilares: PilarSimulador[];
};

export type ResumoSimulador = {
  papel: string;
  papel_rotulo: string;
  pode_ver_outros: boolean;
  visao: Visao;
  visoes: { valor: Visao; rotulo: string }[];
  dias_uteis: { passados: number; total: number; restantes: number };
  precisa_escolher: boolean;
  alvo: PessoaSimulador | null;
  vendo_outro: boolean;
  simulacao: Simulacao | null;
  erro: string | null;
};

export type FiltroSimulador = { visao: Visao; usuario?: number; acelerador?: boolean };

export const simulador = {
  resumo: ({ visao, usuario, acelerador }: FiltroSimulador) =>
    api
      .get<ResumoSimulador>('/m/simulador/', { params: { visao, usuario, acelerador: acelerador ? 1 : undefined }, timeout: TEMPO_MOTOR })
      .then((r) => r.data),
  pessoas: () => api.get<{ resultados: PessoaSimulador[] }>('/m/simulador/pessoas/').then((r) => r.data.resultados),
};
