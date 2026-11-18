/** Módulo Facilitador (/api/mobile/v1/m/facilitador/): arquivos e links úteis do dia a dia. */
import { api } from '../api';

export type TipoRecurso = 'arquivo' | 'link';

export type RecursoResumo = {
  id: number;
  titulo: string;
  descricao: string | null;
  tipo: TipoRecurso;
  categoria: string | null;
  extensao: string | null;
  tamanho: number | null;
  dominio: string | null;
  /** Arquivo HTML que abre como página. */
  pagina: boolean;
  /** Só quem gerencia vê itens ocultos (e eles vêm marcados). */
  oculto: boolean;
};

export type Recurso = RecursoResumo & {
  url: string | null;
  nome_arquivo: string | null;
  tem_arquivo: boolean;
  atualizado_em: string | null;
  acessos: number | null;
  caminho_web: string;
};

export type ListaFacilitador = { categorias: string[]; recursos: RecursoResumo[]; gerencia: boolean };

export type Destino = { tipo: 'link' | 'pagina' | 'arquivo'; url: string };

export const facilitador = {
  async listar(f: { busca?: string; categoria?: string; tipo?: TipoRecurso }) {
    const { data } = await api.get<ListaFacilitador>('/m/facilitador/', { params: f });
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<Recurso>(`/m/facilitador/${id}/`);
    return data;
  },
  /** O endereço para abrir agora (link conta o clique; arquivo vem com link assinado de 5 min). */
  async abrir(id: number, como?: 'arquivo') {
    const { data } = await api.post<Destino>(`/m/facilitador/${id}/abrir/`, como ? { como } : {});
    return data;
  },
};
