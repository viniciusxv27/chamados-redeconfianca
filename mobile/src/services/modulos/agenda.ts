/** Agenda: chamadas a /api/mobile/v1/m/agenda/ e os formatos que voltam. */
import { api } from '../api';

const BASE = '/m/agenda';

export type TipoEvento = 'event' | 'meeting' | 'call' | 'task' | 'reminder' | 'block';

export type Evento = {
  id: number;
  titulo: string;
  tipo: TipoEvento;
  tipo_nome: string;
  cor: string;
  inicio: string;
  fim: string;
  dia_inteiro: boolean;
  local: string | null;
  link: string | null;
  /** Sala de reunião do portal (/reunioes/N/sala/): abre no portal. */
  link_do_portal: boolean;
  privado: boolean;
  recorrente: boolean;
  sou_dono: boolean;
  dono: string | null;
};

export type PessoaAgenda = { id: number; nome: string; setor: string | null };

export type StatusConvite = 'pending' | 'accepted' | 'rejected';

export type DetalheEvento = Evento & {
  descricao: string | null;
  dono_pessoa: PessoaAgenda | null;
  participantes: (PessoaAgenda & { status: StatusConvite; status_nome: string; observacao: string | null })[];
  recorrencia_ate: string | null;
  recorrencia_pai: number | null;
  meu_convite: { id: number; status: StatusConvite; status_nome: string } | null;
  pode_responder: boolean;
  pode_editar: boolean;
  tarefa_id: number | null;
  transcricoes: { id: number; titulo: string; status_nome: string }[];
  url_web: string;
};

export type Semana = {
  inicio: string;
  fim: string;
  hoje: string;
  eventos: Evento[];
  convites_pendentes: number;
  solicitacoes_pendentes: number;
  url_web: string;
};

export type Opcoes = {
  eu: number;
  tipos: { valor: TipoEvento; rotulo: string }[];
  cores: { valor: string; rotulo: string }[];
  pessoas: PessoaAgenda[];
  grupos: { id: number; nome: string; membros: number[] }[];
};

export type CamposEvento = {
  titulo: string;
  descricao: string;
  inicio: string;
  fim: string;
  tipo: TipoEvento;
  cor: string;
  dia_inteiro: boolean;
  local: string;
  /** Fora do pedido = o portal mantém o link (sala da Chamada). */
  link?: string;
  privado: boolean;
  participantes: number[];
  repetir_semanal?: boolean;
  repetir_ate?: string;
};

export type ConviteEvento = { id: number; evento: Evento; convidado_em: string };

export type SolicitacaoReuniao = {
  id: number;
  titulo: string;
  descricao: string | null;
  tipo: string;
  tipo_nome: string;
  inicio: string;
  fim: string;
  local: string | null;
  solicitante: PessoaAgenda | null;
  criada_em: string;
};

export type Resposta = 'aceitar' | 'recusar';

export type StatusTranscricao = 'recording' | 'processing' | 'completed' | 'error';
export type Nivel = 'high' | 'medium' | 'low' | null;

export type TranscricaoResumo = {
  id: number;
  titulo: string;
  status: StatusTranscricao;
  status_nome: string;
  etapa: string | null;
  etapa_rotulo: string | null;
  progresso_pct: number;
  aguardando_nova_tentativa: boolean;
  proxima_tentativa_em: string | null;
  tentativas: number;
  erro: string | null;
  criada_em: string;
  duracao_segundos: number;
  dono: string | null;
  sou_dono: boolean;
  evento: { id: number; titulo: string } | null;
  resumo: string | null;
  acoes: number;
  decisoes: number;
  tarefas: number | null;
};

export type MetaCurta = { id: number; pendente: boolean; url_web: string };

export type TarefaTranscricao = {
  id: number;
  titulo: string;
  descricao: string;
  status: 'PENDING' | 'DOING' | 'DONE';
  status_nome: string;
  prioridade: 'LOW' | 'MEDIUM' | 'HIGH' | 'URGENT';
  prioridade_nome: string;
  prazo: string | null;
  responsavel: PessoaAgenda | null;
  impulso?: { minha_meta: MetaCurta | null; meta_do_responsavel: MetaCurta | null; enviar_para_responsavel_url: string | null };
};

/** No detalhe as decisões vêm como lista (na lista de transcrições é só a contagem). */
export type DetalheTranscricao = Omit<TranscricaoResumo, 'decisoes'> & {
  sentimento: string | null;
  sentimento_nome: string | null;
  tipo_detectado: string | null;
  tags: string[];
  participantes_identificados: string[];
  secoes: { titulo: string; conteudo: string; minutos: number | null; topicos: string[]; destaques: string[] }[];
  decisoes: { texto: string; contexto: string | null; impacto: Nivel }[];
  itens_acao: { texto: string; responsavel: string | null; prazo: string | null; prioridade: Nivel }[];
  riscos: { texto: string; mitigacao: string | null; responsavel: string | null; severidade: Nivel }[];
  lista_tarefas: TarefaTranscricao[];
  pode_atribuir: boolean;
  impulso: { sou_gestor: boolean; gestores: { id: number; nome: string; sou_eu: boolean }[]; hoje: string } | null;
  url_web: string;
};

export type PaginaTranscricoes = { resultados: TranscricaoResumo[]; pagina: number; total: number; tem_mais: boolean; url_web: string };

export type ImportarImpulso = { titulo: string; descricao: string; prazo: string; gestor: number; precisa_aprovacao: boolean };

export const agenda = {
  async semana(inicio: string, fim: string) {
    const { data } = await api.get<Semana>(`${BASE}/eventos/`, { params: { inicio, fim } });
    return data;
  },
  async evento(id: number) {
    const { data } = await api.get<DetalheEvento>(`${BASE}/eventos/${id}/`);
    return data;
  },
  async criar(campos: CamposEvento) {
    const { data } = await api.post<{ evento: DetalheEvento; sala: string | null; mensagem: string }>(`${BASE}/eventos/novo/`, campos);
    return data;
  },
  async editar(id: number, campos: CamposEvento) {
    const { data } = await api.post<{ evento: DetalheEvento; sala: string | null; mensagem: string }>(`${BASE}/eventos/${id}/editar/`, campos);
    return data;
  },
  async excluir(id: number, serie: boolean) {
    const { data } = await api.post<{ ok: boolean; mensagem: string }>(`${BASE}/eventos/${id}/excluir/`, { serie });
    return data;
  },
  async conflitos(inicio: string, fim: string, ignorar?: number) {
    const { data } = await api.get<{ conflitos: Evento[] }>(`${BASE}/eventos/conflitos/`, { params: { inicio, fim, ignorar } });
    return data.conflitos;
  },
  async opcoes() {
    const { data } = await api.get<Opcoes>(`${BASE}/opcoes/`);
    return data;
  },
  async convites() {
    const { data } = await api.get<{ convites: ConviteEvento[]; solicitacoes: SolicitacaoReuniao[] }>(`${BASE}/convites/`);
    return data;
  },
  async responderConvite(id: number, resposta: Resposta, observacao?: string) {
    const { data } = await api.post<{ ok: boolean; mensagem: string; status: StatusConvite; evento_id: number }>(`${BASE}/convites/${id}/responder/`, {
      resposta,
      observacao,
    });
    return data;
  },
  async responderSolicitacao(id: number, resposta: Resposta, observacao?: string) {
    const { data } = await api.post<{ ok: boolean; mensagem: string; status: string; evento_id: number | null }>(`${BASE}/solicitacoes/${id}/responder/`, {
      resposta,
      observacao,
    });
    return data;
  },
  async transcricoes(pagina: number) {
    const { data } = await api.get<PaginaTranscricoes>(`${BASE}/transcricoes/`, { params: { pagina } });
    return data;
  },
  async transcricao(id: number) {
    const { data } = await api.get<DetalheTranscricao>(`${BASE}/transcricoes/${id}/`);
    return data;
  },
  async atribuir(id: number, tarefa: number, usuario: number) {
    const { data } = await api.post<{ mensagem: string; transcricao: DetalheTranscricao }>(`${BASE}/transcricoes/${id}/tarefas/${tarefa}/responsavel/`, {
      user_id: usuario,
    });
    return data;
  },
  async importarImpulso(id: number, tarefa: number, campos: ImportarImpulso) {
    const { data } = await api.post<{ mensagem: string; meta_id: number; aprovada: boolean; transcricao: DetalheTranscricao }>(
      `${BASE}/transcricoes/${id}/tarefas/${tarefa}/impulso/`,
      campos,
    );
    return data;
  },
};
