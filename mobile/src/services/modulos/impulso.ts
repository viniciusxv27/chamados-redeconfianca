/** Impulso: chamadas a /api/mobile/v1/m/impulso/ e os formatos que voltam. */
import { api } from '../api';

const BASE = '/m/impulso';

export type PessoaImpulso = { id: number; nome: string; primeiro_nome: string; foto: string | null };

export type StatusMeta = 'A_FAZER' | 'EM_ANDAMENTO' | 'ENTREGUE' | 'CONCLUIDA';
export type PrioridadeMeta = 'BAIXA' | 'MEDIA' | 'ALTA' | 'CRITICA';

export type Faixa = { nome: string; rotulo: string; regra: string; cor: string; premiada: boolean };

export type Periodo = { inicio: string; fim: string; rotulo: string; em_conferencia: boolean };

export type Pontuacao = {
  percentual: number;
  total: number;
  maximo: number;
  pontos_sem_oportunidade: number;
  faixa: Faixa;
  blocos: { nome: string; cor: string; valor: number; maximo: number; pct: number }[];
  linhas: { bloco: string; item: string; pontos: number; maximo: number; info: string }[];
};

export type ResumoImpulso = {
  periodo: Periodo;
  minha: Pontuacao;
  faixas: Faixa[];
  metas: { abertas: number; atrasadas: number; vencem_hoje: number; entregues: number };
  para_avaliar: number;
  convites: number;
  solicitacoes_pendentes: number;
  is_gestor: boolean;
  no_ranking: boolean;
};

export type LinhaRanking = {
  posicao: number;
  id: number;
  nome: string;
  foto: string | null;
  percentual: number;
  faixa: Faixa;
  confiar: number;
  conectar: number;
  inovar: number;
  total: number;
  empatou: boolean;
  sou_eu: boolean;
};

export type Ranking = { periodo: Periodo; calculado_em: string; minha_posicao: number | null; total: number; linhas: LinhaRanking[] };

export type CardMeta = {
  id: number;
  titulo: string;
  status: StatusMeta;
  status_nome: string;
  prioridade: PrioridadeMeta;
  prioridade_nome: string;
  prazo: string | null;
  dias_para_o_prazo: number | null;
  atrasado: boolean;
  recorrente: boolean;
  colaborador: PessoaImpulso | null;
  gestor: PessoaImpulso | null;
  em_parceria: boolean;
  feitos: number;
  total_itens: number;
  novidades: { comentarios: number; anexos: number; itens: number; entregue: boolean };
  tem_novidade: boolean;
  nota: number | null;
  pode_mover: boolean;
};

export type Coluna = {
  status: StatusMeta;
  rotulo: string;
  total: number;
  restantes: number;
  metas: CardMeta[];
  grupos: { chave: string; titulo: string; ids: number[] }[] | null;
};

export type Convite = {
  id: number;
  meta: { id: number; titulo: string; prazo: string | null };
  dono: PessoaImpulso | null;
  convidado_por: PessoaImpulso | null;
  mensagem: string | null;
};

export type Kanban = {
  colunas: Coluna[];
  convites: Convite[];
  is_gestor: boolean;
  filtros: {
    mes: string;
    q: string;
    colaborador: number | null;
    criadas_por_mim: boolean;
    meses: { valor: string; rotulo: string }[];
    colaboradores: { id: number; nome: string }[] | null;
  };
  url_web: string;
};

export type FiltroKanban = { mes: string; q?: string; colaborador?: number | null; criadasPorMim?: boolean };

export type ComentarioMeta = { id: number; autor: PessoaImpulso | null; texto: string; criado_em: string; sou_eu: boolean };

export type DetalheMeta = {
  id: number;
  titulo: string;
  descricao: string;
  descricao_foto: string | null;
  descricao_audio: string | null;
  status: StatusMeta;
  status_nome: string;
  prioridade: PrioridadeMeta;
  prioridade_nome: string;
  prazo: string | null;
  atrasado: boolean;
  recorrencia: string;
  recorrencia_nome: string;
  apenas_dias_uteis: boolean;
  aprovacao: 'APROVADA' | 'PENDENTE' | 'RECUSADA';
  aprovacao_nome: string;
  motivo_recusa: string | null;
  colaborador: PessoaImpulso | null;
  gestor: PessoaImpulso | null;
  criado_por: PessoaImpulso | null;
  criado_em: string;
  participantes: PessoaImpulso[];
  parcerias: { id: number; convidado: PessoaImpulso | null; convidado_por: PessoaImpulso | null; status: string; status_nome: string; mensagem: string | null }[];
  meu_convite: { id: number; convidado_por: PessoaImpulso | null; mensagem: string | null } | null;
  itens: { id: number; texto: string; concluido: boolean; concluido_por: PessoaImpulso | null; concluido_em: string | null }[];
  feitos: number;
  anexos: { id: number; nome: string; tipo: 'ARQUIVO' | 'LINK'; url: string | null; enviado_por: PessoaImpulso | null; enviado_em: string }[];
  comentarios: ComentarioMeta[];
  entrega_link: string | null;
  entregue_em: string | null;
  avaliacao: {
    nota_qualidade: number;
    nota_prazo: number;
    media: number;
    comentario: string | null;
    avaliado_por: PessoaImpulso | null;
    avaliado_em: string | null;
  } | null;
  pode_mover: boolean;
  status_possiveis: StatusMeta[];
  pode_marcar_itens: boolean;
  pode_comentar: boolean;
  pode_avaliar: boolean;
  url_web: string;
};

function paramsDoFiltro(f: FiltroKanban) {
  return {
    mes: f.mes,
    q: f.q || undefined,
    colaborador: f.colaborador || undefined,
    criadas_por_mim: f.criadasPorMim ? '1' : undefined,
  };
}

export const impulso = {
  async resumo() {
    const { data } = await api.get<ResumoImpulso>(`${BASE}/resumo/`);
    return data;
  },
  async ranking() {
    // A conta do ranking é pesada no portal na primeira vez do mês (depois fica pronta por 3 min).
    const { data } = await api.get<Ranking>(`${BASE}/ranking/`, { timeout: 90000 });
    return data;
  },
  async kanban(f: FiltroKanban) {
    const { data } = await api.get<Kanban>(`${BASE}/metas/`, { params: paramsDoFiltro(f) });
    return data;
  },
  async maisConcluidas(f: FiltroKanban, inicio: number) {
    const { data } = await api.get<{ metas: CardMeta[]; proximo: number; restantes: number }>(`${BASE}/metas/concluidas/`, {
      params: { ...paramsDoFiltro(f), inicio },
    });
    return data;
  },
  async meta(id: number) {
    const { data } = await api.get<DetalheMeta>(`${BASE}/metas/${id}/`);
    return data;
  },
  async mover(id: number, status: StatusMeta) {
    const { data } = await api.post<{ meta: DetalheMeta; proxima: string | null }>(`${BASE}/metas/${id}/status/`, { status });
    return data;
  },
  async marcarItem(itemId: number) {
    const { data } = await api.post<{ id: number; concluido: boolean; feitos: number; total: number }>(`${BASE}/metas/itens/${itemId}/marcar/`);
    return data;
  },
  async comentar(id: number, mensagem: string) {
    const { data } = await api.post<ComentarioMeta>(`${BASE}/metas/${id}/comentarios/`, { mensagem });
    return data;
  },
  async avaliar(id: number, notas: { nota_qualidade: number; nota_prazo: number; comentario?: string }) {
    const { data } = await api.post<{ mensagem: string; meta: DetalheMeta }>(`${BASE}/metas/${id}/avaliar/`, notas);
    return data;
  },
  async responderConvite(id: number, resposta: 'aceitar' | 'recusar') {
    const { data } = await api.post<{ id: number; status: string; meta_id: number; mensagem: string }>(`${BASE}/convites/${id}/responder/`, { resposta });
    return data;
  },
};
