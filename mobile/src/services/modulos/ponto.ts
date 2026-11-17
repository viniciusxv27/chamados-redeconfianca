/**
 * Módulo Ponto (/api/mobile/v1/m/ponto/): Meu ponto (Tangerino, só leitura),
 * Solicitações ao DP, férias, escala e a caixa do DP (para quem analisa).
 * O app nunca bate ponto: isso continua no relógio/app da Sólides.
 */
import { api } from '../api';
import type { Pagina, Pessoa } from '../tipos';

const BASE = '/m/ponto';

export type Opcao = { valor: string; rotulo: string };

export type ResumoPonto = {
  secoes: { marcacoes: boolean; ferias: boolean; solicitacoes: boolean; escala: boolean; caixa_dp: boolean };
  vinculado: boolean;
  solicitacoes: { abertas: number; devolvidas: number; total: number };
  dp_pendentes: number;
};

export type Batida = { tipo: 'ENTRADA' | 'SAIDA'; hora: string; iso: string; editado: boolean };

export type DiaPonto = {
  dia: string;
  semana: string;
  eventos: Batida[];
  horas: string;
  segundos: number;
  previsto: string | null;
  previsto_segundos: number | null;
  diferenca_segundos: number | null;
  aberto: boolean;
  fim_de_semana: boolean;
  sem_marcacao: boolean;
};

export type Totais = { feito: string | null; previsto: string | null; diferenca: string | null; diferenca_segundos: number | null };

export type AjusteSolides = {
  id: number;
  tipo: string;
  tipo_nome: string;
  dia: string;
  resumo: string;
  motivo: string;
  status: 'PENDENTE' | 'APLICADA' | 'RECUSADA' | 'ERRO' | 'CANCELADA';
  status_nome: string;
  resposta: string | null;
  criado_em: string;
  pode_cancelar: boolean;
};

export type PainelPonto = {
  hoje: string;
  inicio_semana: string;
  integracao_ativa: boolean;
  vinculado: boolean;
  isento: boolean;
  erro: string | null;
  status: {
    situacao: 'SEM_ENTRADA' | 'TRABALHANDO' | 'EM_INTERVALO' | 'ENCERRADO';
    rotulo: string;
    eventos: Batida[];
    trabalhado: string;
    trabalhado_segundos: number;
    dentro: boolean;
    desde: string | null;
    marcos: { rotulo: string; feito: boolean }[];
  } | null;
  pendencias: { dia: string; entrada: string; hora: string }[];
  dias: DiaPonto[];
  semana: Totais | null;
  periodo: Totais | null;
  jornada: { nome: string; horas_semana: number; resumo: string } | null;
  tem_jornada: boolean;
  ajustes_solides: { ligado: boolean; pedidos: AjusteSolides[] };
};

export type TipoSolicitacao = 'AJUSTE' | 'ATESTADO' | 'DECLARACAO';
export type StatusSolicitacao = 'ENVIADA' | 'EM_ANALISE' | 'DEVOLVIDA' | 'APROVADA' | 'CANCELADA';

export type SolicitacaoResumo = {
  id: number;
  tipo: TipoSolicitacao;
  tipo_nome: string;
  titulo: string;
  status: StatusSolicitacao;
  status_nome: string;
  aberta: boolean;
  data: string;
  periodo: string;
  enviada_em: string;
  dias_esperando: number;
  ajustado_no_ponto: boolean;
  anexos: number;
  colaborador?: Pessoa;
  responsavel?: Pessoa | null;
};

export type AnexoSolicitacao = {
  id: number;
  nome: string;
  tipo: string;
  tamanho: number;
  imagem: boolean;
  pdf: boolean;
  removido: boolean;
  enviado_em: string;
  url: string;
};

export type EventoSolicitacao = {
  id: number;
  acao: 'CRIADA' | 'REENVIADA' | 'EM_ANALISE' | 'APROVADA' | 'DEVOLVIDA' | 'CANCELADA' | 'COMENTARIO' | 'AJUSTADA';
  acao_nome: string;
  autor: Pessoa | null;
  texto: string | null;
  alteracoes: { campo: string; antes: string; depois: string }[];
  em: string;
};

export type Solicitacao = SolicitacaoResumo & {
  especie: string | null;
  especie_nome: string | null;
  data_fim: string | null;
  hora_inicio: string | null;
  hora_fim: string | null;
  dias: number;
  motivo_ajuste: string | null;
  motivo_nome: string | null;
  marcacoes: { rotulo: string; hora: string }[];
  justificativa: string;
  resposta: string | null;
  ajustado_em: string | null;
  aprovada_em: string | null;
  registrado: string[] | null;
  anexos_lista: AnexoSolicitacao[];
  anexos_removidos: AnexoSolicitacao[];
  eventos: EventoSolicitacao[];
  outras: SolicitacaoResumo[];
  dono: boolean;
  dp: boolean;
  pode_corrigir: boolean;
  pode_cancelar: boolean;
  pode_analisar: boolean;
  pode_assumir: boolean;
  pode_lancar: boolean;
  pode_comentar: boolean;
  url_web: string;
};

export type Formulario = {
  tipos: Opcao[];
  especies: Record<'ATESTADO' | 'DECLARACAO', Opcao[]>;
  motivos: Opcao[];
  rotulos_marcacao: string[];
  extensoes: string[];
  tamanho_max_mb: number;
  anexos_max: number;
  dias_para_tras_ajuste: number;
  dias_atestado_max: number;
  hoje: string;
};

export type FiltroSolicitacoes = '' | 'abertas' | StatusSolicitacao;

export type ListaSolicitacoes = Pagina<SolicitacaoResumo> & {
  contagem: { abertas: number; devolvidas: number; aprovadas: number; total: number };
};

export type FiltroCaixa = 'pendentes' | 'devolvidas' | 'lancar' | 'aprovadas' | 'todas';

export type Caixa = Pagina<SolicitacaoResumo> & {
  filtro: FiltroCaixa;
  kpis: { aguardando: number; em_analise: number; devolvidas: number; lancar: number; aprovadas_mes: number; atrasadas: number };
};

export type Ferias = {
  integracao_ativa: boolean;
  vinculado: boolean;
  disponivel: boolean;
  motivo?: string | null;
  admissao?: string | null;
  saldo_total?: number;
  dias_vencidos?: number;
  em_gozo?: Lancamento | null;
  volta_em?: string | null;
  dias_restantes_gozo?: number;
  proxima?: Lancamento | null;
  programadas?: Lancamento[];
  passadas?: Lancamento[];
  periodos?: PeriodoFerias[];
  vencendo?: number;
};

export type Lancamento = { inicio: string; fim: string; dias: number; status: string };

export type PeriodoFerias = {
  numero: number;
  inicio: string;
  fim: string;
  completo: boolean;
  concessivo_fim: string;
  direito: number;
  gozados: number;
  saldo: number;
  vencido: boolean;
  vencendo: boolean;
  sem_dados: boolean;
  dias_para_vencer: number;
};

export type DiaEscala = {
  data: string;
  nome: string;
  hoje: boolean;
  tipo: 'TRABALHO' | 'FOLGA' | 'FERIADO' | null;
  entrada: string | null;
  saida_almoco: string | null;
  volta_almoco: string | null;
  saida: string | null;
  observacao: string | null;
  horas: number;
};

export type Escala = {
  semana_inicio: string;
  semana_fim: string;
  anterior: string;
  proxima: string;
  dias: DiaEscala[];
  tem_escala: boolean;
  horas: number;
  horas_meta: number;
  abaixo_da_meta: boolean;
  monta_escala: boolean;
};

export type ArquivoLocal = { uri: string; nome: string; tipo: string; tamanho?: number };

/** Campos do formulário do portal (solicitacoes_dp/servicos.py:ler): horas vão como hora_0…hora_5. */
export type CamposSolicitacao = {
  tipo: TipoSolicitacao;
  data: string;
  justificativa: string;
  motivo_ajuste?: string;
  horas?: Record<number, string>;
  especie?: string;
  data_fim?: string;
  hora_inicio?: string;
  hora_fim?: string;
  anexos?: ArquivoLocal[];
  /** Só na correção. */
  comentario?: string;
  remover?: number[];
};

export type AcaoSolicitacao =
  | { acao: 'cancelar' | 'comentar' | 'devolver'; texto?: string }
  | { acao: 'assumir' | 'lancar' }
  | { acao: 'aprovar'; texto?: string; arquivar?: boolean; ajustado?: boolean };

function formulario(campos: CamposSolicitacao) {
  const corpo = new FormData();
  corpo.append('tipo', campos.tipo);
  corpo.append('data', campos.data);
  corpo.append('justificativa', campos.justificativa);
  if (campos.motivo_ajuste) corpo.append('motivo_ajuste', campos.motivo_ajuste);
  for (const [i, hora] of Object.entries(campos.horas ?? {})) if (hora) corpo.append(`hora_${i}`, hora);
  if (campos.especie) corpo.append('especie', campos.especie);
  if (campos.data_fim) corpo.append('data_fim', campos.data_fim);
  if (campos.hora_inicio) corpo.append('hora_inicio', campos.hora_inicio);
  if (campos.hora_fim) corpo.append('hora_fim', campos.hora_fim);
  if (campos.comentario) corpo.append('comentario', campos.comentario);
  for (const id of campos.remover ?? []) corpo.append('remover', String(id));
  for (const a of campos.anexos ?? []) {
    // React Native aceita {uri, name, type} no FormData.
    corpo.append('anexos', { uri: a.uri, name: a.nome, type: a.tipo } as unknown as Blob);
  }
  return corpo;
}

const multipart = { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 };

export const ponto = {
  async resumo() {
    const { data } = await api.get<ResumoPonto>(`${BASE}/resumo/`);
    return data;
  },
  async marcacoes() {
    const { data } = await api.get<PainelPonto>(`${BASE}/marcacoes/`, { timeout: 45000 });
    return data;
  },
  async cancelarAjusteSolides(id: number) {
    const { data } = await api.post<AjusteSolides>(`${BASE}/ajustes-solides/${id}/cancelar/`);
    return data;
  },
  async ferias() {
    const { data } = await api.get<Ferias>(`${BASE}/ferias/`, { timeout: 45000 });
    return data;
  },
  async escala(inicio?: string) {
    const { data } = await api.get<Escala>(`${BASE}/escala/`, { params: inicio ? { inicio } : undefined });
    return data;
  },
  async formulario() {
    const { data } = await api.get<Formulario>(`${BASE}/formulario/`);
    return data;
  },
  async registradoNoDia(dia: string) {
    const { data } = await api.get<{ horas: string[] | null }>(`${BASE}/marcacoes-do-dia/`, { params: { data: dia } });
    return data.horas;
  },
  async solicitacoes(filtro: FiltroSolicitacoes, pagina: number) {
    const { data } = await api.get<ListaSolicitacoes>(`${BASE}/solicitacoes/`, { params: { status: filtro || undefined, pagina } });
    return data;
  },
  async solicitacao(id: number) {
    const { data } = await api.get<Solicitacao>(`${BASE}/solicitacoes/${id}/`);
    return data;
  },
  async enviar(campos: CamposSolicitacao) {
    const { data } = await api.post<Solicitacao>(`${BASE}/solicitacoes/`, formulario(campos), multipart);
    return data;
  },
  async corrigir(id: number, campos: CamposSolicitacao) {
    const { data } = await api.post<Solicitacao>(`${BASE}/solicitacoes/${id}/corrigir/`, formulario(campos), multipart);
    return data;
  },
  async acao(id: number, acao: AcaoSolicitacao) {
    const { data } = await api.post<Solicitacao>(`${BASE}/solicitacoes/${id}/acao/`, acao);
    return data;
  },
  async caixa(filtro: FiltroCaixa, busca: string, pagina: number) {
    const { data } = await api.get<Caixa>(`${BASE}/caixa/`, { params: { filtro, q: busca || undefined, pagina } });
    return data;
  },
};
