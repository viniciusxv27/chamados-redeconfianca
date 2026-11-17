/** Módulo Documentos: os documentos atribuídos para eu assinar (/documentos/ do portal). */
import { api } from '../api';

export type SituacaoDocumentos = 'todos' | 'pendentes' | 'assinados';

export type CategoriaDocumento = { id: number; nome: string; cor: string | null };

export type DocumentoResumo = {
  /** Id da atribuição (o mesmo da URL /documentos/<id>/ da web). */
  id: number;
  documento_id: number;
  titulo: string;
  descricao: string | null;
  categoria: CategoriaDocumento | null;
  assinado: boolean;
  assinado_em: string | null;
  atribuido_em: string | null;
  obrigatorio: boolean;
};

export type DocumentoDetalhe = DocumentoResumo & {
  de_outra_pessoa: { id: number; nome: string; primeiro_nome: string; foto: string | null; setor: string | null } | null;
  pode_assinar: boolean;
  assinatura: { em: string | null; ip: string | null; hash: string | null; imagem: string | null } | null;
  /** Links assinados (valem 5 min). */
  pdf: string | null;
  pdf_assinado: string | null;
  caminho_web: string;
};

export type ListaDocumentos = {
  pendentes: number;
  assinados: number;
  categorias: CategoriaDocumento[];
  documentos: DocumentoResumo[];
  administra: boolean;
};

export const documentos = {
  async listar(situacao: SituacaoDocumentos) {
    const { data } = await api.get<ListaDocumentos>('/m/documentos/', { params: situacao === 'todos' ? undefined : { situacao } });
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<DocumentoDetalhe>(`/m/documentos/${id}/`);
    return data;
  },
  async assinar(id: number, assinatura: string) {
    const { data } = await api.post<DocumentoDetalhe>(`/m/documentos/${id}/assinar/`, { signature: assinatura });
    return data;
  },
};
