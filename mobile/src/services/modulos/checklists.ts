/**
 * Módulo Checklists ADM (/api/mobile/v1/m/checklists/): meus checklists do dia,
 * preencher item a item, evidências e o acompanhamento do gestor (só leitura).
 * Enviar, subir e excluir evidência passam pelas próprias views do portal: as
 * mensagens de erro são as mesmas da web.
 */
import { api } from '../api';
import type { Pagina, Pessoa } from '../tipos';

const BASE = '/m/checklists';

export type StatusExecucao = 'pending' | 'in_progress' | 'completed' | 'overdue' | 'awaiting_approval';
export type TipoTarefa = 'normal' | 'yes_no' | 'dropdown';
export type Situacao = 'yes' | 'no' | 'not_applicable';

export type ExecucaoResumo = {
  id: number;
  checklist: string;
  setor: string | null;
  data: string;
  periodo: 'morning' | 'afternoon';
  periodo_nome: string;
  status: StatusExecucao;
  status_nome: string;
  total: number;
  feitas: number;
  progresso: number;
  atrasado: boolean;
  pode_preencher: boolean;
  enviado_em: string | null;
  responsavel?: Pessoa | null;
};

export type Evidencia = {
  id: number | null;
  tipo: 'image' | 'video' | 'document';
  nome: string;
  imagem: boolean;
  enviada_em: string | null;
  url: string;
  removivel: boolean;
};

export type Instrucao = { tipo: 'image' | 'video' | 'document'; titulo: string; url: string };

export type Tarefa = {
  id: number;
  tarefa_id: number;
  titulo: string;
  descricao: string;
  tipo: TipoTarefa;
  obrigatoria: boolean;
  pontos: number;
  feita: boolean;
  resposta: 'yes' | 'no' | null;
  situacao: Situacao | null;
  observacao: string;
  concluida_em: string | null;
  aprovacao: { status: 'pending' | 'approved' | 'rejected'; nome: string; observacao: string | null; em: string | null };
  evidencias: Evidencia[];
  instrucoes: Instrucao[];
};

export type Execucao = ExecucaoResumo & {
  descricao: string;
  atribuido_por: Pessoa | null;
  iniciado_em: string | null;
  concluido_em: string | null;
  observacoes: string;
  sou_executor: boolean;
  tarefas: Tarefa[];
  url_web: string;
};

export type InicioChecklists = {
  hoje: string;
  execucoes: ExecucaoResumo[];
  contadores: { hoje_abertos: number; hoje_enviados: number; atrasados: number; atrasados_dias: number; aguardando_aprovacao: number };
  gestor: boolean;
  url_web: string;
};

export type FiltroExecucoes = 'pendentes' | 'enviados' | 'todos';

/** O que o app guarda enquanto a pessoa preenche (vai tudo junto no "Enviar"). */
export type Resposta = { id: number; feita?: boolean; resposta?: 'yes' | 'no' | null; situacao?: Situacao | '' | null; observacao?: string };

export type Turno = { total: number; pendentes: number; andamento: number; feitos: number };

export type LinhaPessoa = { pessoa: Pessoa; manha: Turno; tarde: Turno; atrasados: number; total: number; feitos: number; taxa: number };

export type Loja = { loja: string; pessoas: LinhaPessoa[]; total: number; feitos: number; faltam: number; atrasados: number; taxa: number };

export type Acompanhamento = {
  data: string;
  limite_atraso: string;
  totais: { pessoas: number; total: number; feitos: number; faltam: number; atrasados: number; taxa: number };
  lojas: Loja[];
  url_web: string;
};

export type AcompanhamentoPessoa = { data: string; pessoa: Pessoa; do_dia: ExecucaoResumo[]; atrasados: ExecucaoResumo[] };

export type ArquivoLocal = { uri: string; nome: string; tipo: string };

export const checklists = {
  async inicio() {
    const { data } = await api.get<InicioChecklists>(`${BASE}/inicio/`);
    return data;
  },
  async execucoes(filtro: FiltroExecucoes, pagina: number) {
    const { data } = await api.get<Pagina<ExecucaoResumo> & { filtro: FiltroExecucoes }>(`${BASE}/execucoes/`, { params: { filtro, pagina } });
    return data;
  },
  async execucao(id: number) {
    const { data } = await api.get<Execucao>(`${BASE}/execucoes/${id}/`);
    return data;
  },
  async enviar(id: number, respostas: Resposta[]) {
    const { data } = await api.post<{ mensagem: string; execucao: Execucao }>(`${BASE}/execucoes/${id}/enviar/`, { respostas }, { timeout: 60000 });
    return data;
  },
  async subirEvidencias(tarefaId: number, arquivos: ArquivoLocal[]) {
    const corpo = new FormData();
    // React Native aceita {uri, name, type} no FormData.
    for (const a of arquivos) corpo.append('files', { uri: a.uri, name: a.nome, type: a.tipo } as unknown as Blob);
    const { data } = await api.post<{ mensagem: string; tarefa: Tarefa }>(`${BASE}/tarefas/${tarefaId}/evidencias/`, corpo, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    });
    return data;
  },
  async excluirEvidencia(id: number) {
    const { data } = await api.post<{ mensagem: string; tarefa: Tarefa }>(`${BASE}/evidencias/${id}/excluir/`);
    return data;
  },
  async acompanhamento(dia: string) {
    const { data } = await api.get<Acompanhamento>(`${BASE}/acompanhamento/`, { params: { data: dia }, timeout: 45000 });
    return data;
  },
  async acompanhamentoPessoa(id: number, dia: string) {
    const { data } = await api.get<AcompanhamentoPessoa>(`${BASE}/acompanhamento/pessoas/${id}/`, { params: { data: dia } });
    return data;
  },
};
