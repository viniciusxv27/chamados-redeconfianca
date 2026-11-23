/** Módulo Simulador: metas, realizado, projeção e prêmio estimado por pilar (/simulator/ do portal). */
import { api } from '../api';

/** A primeira consulta do mês busca o realizado no MySQL (até ~30 s); depois fica em cache por 15 min. */
const TEMPO_MOTOR = 90_000;

export type Visao = 'projecao' | 'realizado' | 'simulador';

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

export type CampoSimulador = { chave: string; rotulo: string; dinheiro: boolean };

/** A tabela de valores da visão Simulador (colunas Meta, Real Individual e Real PDV/Coordenação). */
export type FormularioSimulador = {
  colunas: { chave: 'meta' | 'real' | 'realpdv'; rotulo: string }[];
  pilares: { chave: string; rotulo: string; quantidade: boolean; campos: Partial<Record<'meta' | 'real' | 'realpdv', CampoSimulador[]>>; campo_diario: string }[];
  /** 1ª entrada: os campos vêm com o realizado do mês. */
  primeira: boolean;
  valores: Record<string, string>;
  /** Meta por pilar (chave do formulário) para a meta-diária. */
  metas: Record<string, number>;
  pode_copiar: boolean;
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
  simulador: FormularioSimulador | null;
  pilares_hunter: { chave: string; rotulo: string }[];
  hunter: Record<string, number>;
  /** A tabela da rede (/simulator/tabela/): quem vê o comissionamento. */
  pode_ver_tabela: boolean;
};

export type LinhaTabela = {
  id: number;
  nome: string;
  papel: string;
  papel_rotulo: string;
  setor: string | null;
  coordenador: string | null;
  realizado: number;
  projecao: number;
  diferenca: number;
  motivo: string;
  tem_dado: boolean;
  erro: string | null;
  fatores: { tipo: 'pilar' | 'extra'; rotulo: string; delta: number; realizado: number; projecao: number; atingimento: number | null }[];
};

export type TabelaRede = {
  status: 'calculando' | 'pronto';
  desatualizado: boolean;
  gerado_em: string | null;
  referencia: string;
  resumo: { pessoas: number; com_dados: number; realizado: number; projecao: number; diferenca: number; media_projecao: number };
  papeis: { chave: string; rotulo: string; quantos: number }[];
  linhas: LinhaTabela[];
};

export type FiltroSimulador = {
  visao: Visao;
  usuario?: number;
  acelerador?: boolean;
  /** Nível Hunter por pilar (0, 2 ou 3). */
  hunter?: Record<string, number>;
  /** Visão Simulador: os valores digitados (chave do campo → texto). */
  entradas?: Record<string, string>;
};

function parametros({ visao, usuario, acelerador, hunter, entradas }: FiltroSimulador) {
  const p: Record<string, string | number> = { visao };
  if (usuario) p.usuario = usuario;
  if (acelerador) p.acelerador = 1;
  for (const [k, v] of Object.entries(hunter ?? {})) if (v) p[`hunter_${k}`] = v;
  if (visao === 'simulador') for (const [k, v] of Object.entries(entradas ?? {})) if (v !== '') p[`sim__${k}`] = v;
  return p;
}

export const simulador = {
  resumo: (filtro: FiltroSimulador) => api.get<ResumoSimulador>('/m/simulador/', { params: parametros(filtro), timeout: TEMPO_MOTOR }).then((r) => r.data),
  /** "Copiar Projeção / Copiar Realizado": os valores de toda a tabela naquela visão. */
  valores: (visao: 'projecao' | 'realizado', usuario?: number) =>
    api
      .get<{ visao: string; valores: Record<'meta' | 'real' | 'realpdv', Record<string, string>>; metas: Record<string, number> }>('/m/simulador/valores/', {
        params: { visao, usuario },
        timeout: TEMPO_MOTOR,
      })
      .then((r) => r.data),
  tabela: () => api.get<TabelaRede>('/m/simulador/tabela/').then((r) => r.data),
  pessoas: () => api.get<{ resultados: PessoaSimulador[] }>('/m/simulador/pessoas/').then((r) => r.data.resultados),
};
