/**
 * Módulo Cursos (/api/mobile/v1/m/cursos/): cursos Vivo e o comprovante.
 * O curso acontece na plataforma da Vivo; o portal só guarda o comprovante.
 * Publicar curso, aprovar comprovante e cobrar as lojas continuam no portal.
 */
import { api } from '../api';

const BASE = '/m/cursos';

export type SituacaoCurso = 'NAO_FEZ' | 'RECUSADO' | 'CONFERIR' | 'APROVADO';

export type EnvioCurso = {
  id: number;
  status: 'PENDENTE' | 'APROVADO' | 'RECUSADO';
  status_nome: string;
  nome: string;
  tamanho: number;
  enviado_em: string;
  observacao: string | null;
  revisado_em: string | null;
  /** Link assinado (vale 5 min). */
  url: string | null;
};

export type CursoResumo = {
  id: number;
  tipo: 'FOCO' | 'CAPACITACAO';
  tipo_nome: string;
  titulo: string;
  competencia: string | null;
  prazo: string;
  dias: number;
  tem_link: boolean;
  situacao: SituacaoCurso;
  situacao_nome: string;
  entregue: boolean;
  atrasado: boolean;
  ultimo_envio: EnvioCurso | null;
};

export type MeusCursos = {
  cursos: CursoResumo[];
  resumo: { nao_fez: number; recusados: number; conferir: number; aprovados: number; atrasados: number; pendentes: number; total: number };
  gestor: boolean;
  trava_portal: boolean;
  tamanho_maximo: number;
  extensoes: string;
};

export type CursoDetalhe = CursoResumo & {
  orientacoes: string | null;
  link: string | null;
  envios: EnvioCurso[];
  pode_enviar: boolean;
  tamanho_maximo: number;
  extensoes: string;
  trava_portal: boolean;
  caminho_web: string;
  mensagem?: string;
};

export type PessoaQuadro = { id: number; nome: string; situacao: SituacaoCurso; situacao_nome: string; entregue: boolean; envio: EnvioCurso | null };

export type LojaQuadro = { nome: string; geral: boolean; total: number; feitos: number; faltam: number; conferir: number; percentual: number; pessoas: PessoaQuadro[] };

export type QuadroGestao = {
  cursos: { id: number; titulo: string; prazo: string; tipo_nome: string }[];
  curso: { id: number; titulo: string; tipo: string; tipo_nome: string; prazo: string; vencido: boolean } | null;
  totais: { pessoas: number; entregues: number; conferir: number; aprovados: number; recusados: number; nao_fez: number };
  percentual: number;
  lojas: LojaQuadro[];
  caminho_web: string;
};

export type ArquivoComprovante = { uri: string; nome: string; tipo: string; tamanho?: number };

export const cursos = {
  async meus() {
    const { data } = await api.get<MeusCursos>(`${BASE}/`);
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<CursoDetalhe>(`${BASE}/${id}/`);
    return data;
  },
  async enviarComprovante(id: number, arquivo: ArquivoComprovante) {
    const corpo = new FormData();
    // React Native aceita {uri, name, type} no FormData.
    corpo.append('arquivo', { uri: arquivo.uri, name: arquivo.nome, type: arquivo.tipo } as unknown as Blob);
    const { data } = await api.post<CursoDetalhe>(`${BASE}/${id}/comprovante/`, corpo, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 });
    return data;
  },
  async gestao(curso?: number) {
    const { data } = await api.get<QuadroGestao>(`${BASE}/gestao/`, { params: curso ? { curso } : undefined });
    return data;
  },
};
