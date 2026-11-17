/** Módulo Quadro de HC: o quadro de /quadro-hc/ (rede → carteira → loja → pessoas), previsto × real. */
import { api } from '../api';

export type PapelQuadro = 'SUPERADMIN' | 'GERENCIAR' | 'VER' | '';
export type SituacaoHC = 'falta' | 'sobra' | 'ok' | 'sem_previsto';
export type Rapido = 'alerta' | 'vaga' | 'novo' | 'experiencia' | 'demissao' | 'ajustado';
export type SituacaoAjuste = 'AUTO' | 'INCLUIR' | 'EXCLUIR';

export type FiltroQuadro = {
  busca?: string;
  carteira?: number | null;
  loja?: number | null;
  cargo?: string;
  rapido?: Rapido | '';
  sem_gerente?: boolean;
  hc?: 'falta' | 'sobra' | '';
};

export type NumerosHC = {
  real: number;
  previsto: number;
  falta: number;
  sobra: number;
  lojas_com_previsto: number;
  lojas_com_falta: number;
  lojas_com_sobra: number;
};

export type ComparacaoFuncao = { grupo: string; real: number; previsto: number; diferenca: number };

export type LojaResumo = {
  id: number;
  nome: string;
  adabas: string;
  e_loja: boolean;
  carteira_id: number;
  carteira: string;
  total: number;
  total_previsto: number;
  /** total − previsto (null se a loja não tem HC previsto). */
  diferenca: number | null;
  falta: number;
  sobra: number;
  situacao_hc: SituacaoHC;
  sem_gerente: boolean;
  vagas: number;
  resumo: { grupo: string; n: number }[];
  comparacao: ComparacaoFuncao[];
  marcas: { demissao: number; ajustados: number; experiencia: number; novos: number; alerta: number };
  /** Itens (pessoas e vagas) que batem com o filtro; null sem filtro. */
  encontrados: number | null;
};

export type CarteiraHC = {
  id: number;
  nome: string;
  coordenacao: string;
  total: number;
  vagas: number;
  hc: NumerosHC;
  lojas: LojaResumo[];
};

export type Quadro = {
  hoje: string;
  papel: PapelQuadro;
  pode_gerenciar: boolean;
  kpis: {
    total: number;
    lojas: number;
    vagas: number;
    vagas_previstas: number;
    sem_gerente: number;
    exp_proximas: number;
    demissao_antiga: number;
    ajustes: number;
    agendadas: number;
    para_hoje: number;
  };
  hc: NumerosHC;
  filtrando: boolean;
  resultados: number | null;
  todas_carteiras: { id: number; nome: string; total: number }[];
  carteiras: CarteiraHC[];
  sem_loja: { id: number; nome: string; cargo: string }[];
  fora: { id: number; nome: string; cargo: string; loja: string; motivo: string }[];
};

export type AjustePessoa = { funcao: string; setor: number | null; setor_nome: string; situacao: SituacaoAjuste; motivo: string } | null;

export type PessoaHC = {
  id: number;
  nome: string;
  iniciais: string;
  cargo: string;
  grupo: string;
  /** De onde veio a função: 'cargo' do cadastro, 'grupo' GERENTES ou ajuste 'manual'. */
  origem_funcao: 'cargo' | 'grupo' | 'manual';
  cargo_grupo: string;
  /** '' = ativo; senão o rótulo do status (Férias, Afastado…). */
  status: string;
  situacao?: string;
  ferias_inicio?: string | null;
  ferias_fim?: string | null;
  retorno?: string | null;
  login: string;
  cidade: string;
  telefone: string;
  contratacao: string | null;
  nascimento: string | null;
  exp1: string | null;
  exp2: string | null;
  exp1_alerta: '' | 'proxima' | 'passou';
  exp2_alerta: '' | 'proxima' | 'passou';
  novo: boolean;
  sai_em: string | null;
  demissao_antiga: string | null;
  observacao: string;
  agendadas: { id: number; data: string; resumo: string }[];
  ajuste: AjustePessoa;
  ajustado: boolean;
  alerta: boolean;
  experiencia: boolean;
  e_consultor: boolean;
  meta_pct: number | null;
  setor_id: number | null;
  setor_cadastro: number | null;
};

export type LojaDetalhe = {
  loja: LojaResumo;
  coordenacao: string;
  pode_gerenciar: boolean;
  filtrando: boolean;
  pessoas: PessoaHC[];
  vagas: { id: number; cargo: string; observacao: string; aberta_em: string | null; aberta_por: string }[];
  vagas_previstas: { grupo: string; real: number; previsto: number }[];
  previsto: { funcao: string; quantidade: number }[];
};

export type PessoaDetalhe =
  | (PessoaHC & {
      fora: false;
      loja: { id: number; nome: string } | null;
      carteira: string;
      historico: { quando: string; frase: string; origem: 'quadro' | 'cadastro' }[];
      pode_gerenciar: boolean;
    })
  | { id: number; nome: string; cargo: string; fora: true; loja_cadastro: string; ajuste: AjustePessoa; pode_gerenciar: boolean };

export type OpcoesQuadro = {
  cargos: string[];
  funcoes: string[];
  situacoes_ajuste: { valor: SituacaoAjuste; rotulo: string }[];
  setores: { id: number; nome: string }[];
};

export type NovoAjuste = { colaborador: number; funcao: string; setor: number | null; situacao: SituacaoAjuste; motivo: string };

function parametros(f: FiltroQuadro) {
  const p: Record<string, string | number> = {};
  if (f.busca) p.busca = f.busca;
  if (f.carteira || f.carteira === 0) p.carteira = f.carteira;
  if (f.loja) p.loja = f.loja;
  if (f.cargo) p.cargo = f.cargo;
  if (f.rapido) p.rapido = f.rapido;
  if (f.sem_gerente) p.sem_gerente = 1;
  if (f.hc) p.hc = f.hc;
  return p;
}

export const quadroHC = {
  async quadro(filtro: FiltroQuadro) {
    const { data } = await api.get<Quadro>('/m/quadro_hc/', { params: parametros(filtro) });
    return data;
  },
  async opcoes() {
    const { data } = await api.get<OpcoesQuadro>('/m/quadro_hc/opcoes/');
    return data;
  },
  async loja(id: number, filtro: FiltroQuadro) {
    const { data } = await api.get<LojaDetalhe>(`/m/quadro_hc/loja/${id}/`, { params: parametros(filtro) });
    return data;
  },
  async pessoa(id: number) {
    const { data } = await api.get<PessoaDetalhe>(`/m/quadro_hc/pessoa/${id}/`);
    return data;
  },
  async salvarAjuste(ajuste: NovoAjuste) {
    const { data } = await api.post<{ ok: boolean; msg: string }>('/m/quadro_hc/ajuste/', ajuste);
    return data;
  },
  async salvarPrevisto(loja: number, quantidades: Record<string, number>) {
    const { data } = await api.post<{ ok: boolean; msg: string; loja: LojaDetalhe | null }>(`/m/quadro_hc/loja/${loja}/previsto/`, { quantidades });
    return data;
  },
};
