/** Módulo Contracheque: meus holerites, informes de rendimentos e assinatura (/contracheque/ do portal). */
import { api } from '../api';

/** Valores em reais chegam como texto decimal ("2345.67"): use `reais()` de utils/dinheiro. */
export type HoleriteResumo = {
  id: number;
  mes: number;
  ano: number;
  competencia: string;
  mes_nome: string;
  liquido: string | null;
  proventos: string | null;
  descontos: string | null;
  assinado: boolean;
  assinado_em: string | null;
};

export type InformeResumo = {
  id: number;
  ano_base: number;
  exercicio: number;
  total_rendimentos: string | null;
  irrf: string | null;
};

export type PessoaResumo = { id: number; nome: string; primeiro_nome: string; foto: string | null; setor: string | null };

export type RegistroAssinatura = { em: string | null; ip: string | null; hash: string | null; imagem: string | null };

export type HoleriteDetalhe = HoleriteResumo & {
  /** Só vem quando quem abre é da administração vendo o holerite de outra pessoa. */
  de_outra_pessoa: PessoaResumo | null;
  funcionario: { nome: string | null; cpf: string | null; cargo: string | null; admissao: string | null; departamento: string | null };
  salario_base: string | null;
  itens_proventos: { descricao: string; valor: string }[];
  itens_descontos: { descricao: string; valor: string }[];
  bases: { rotulo: string; valor: string | null }[];
  pode_assinar: boolean;
  assinatura: RegistroAssinatura | null;
  tem_pdf: boolean;
  /** Links assinados (valem 5 min): peça o detalhe de novo se expirar. */
  pdf: string | null;
  pdf_assinado: string | null;
  caminho_web: string;
};

export type InformeDetalhe = InformeResumo & {
  de_outra_pessoa: PessoaResumo | null;
  beneficiario: { nome: string | null; cpf: string | null };
  total_isentos: string | null;
  secoes: { titulo: string; linhas: { rotulo: string; valor: string | null }[] }[];
  pdf: string | null;
  caminho_web: string;
};

export type InicioContracheque = {
  anos: number[];
  pendentes: number;
  contracheques: HoleriteResumo[];
  informes: InformeResumo[];
  administra: boolean;
};

export const contracheque = {
  async inicio() {
    const { data } = await api.get<InicioContracheque>('/m/contracheque/');
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<HoleriteDetalhe>(`/m/contracheque/${id}/`);
    return data;
  },
  async informe(id: number) {
    const { data } = await api.get<InformeDetalhe>(`/m/contracheque/informes/${id}/`);
    return data;
  },
  /** `assinatura` = data URL PNG (o mesmo formato do quadro da web). */
  async assinar(id: number, assinatura: string) {
    const { data } = await api.post<HoleriteDetalhe>(`/m/contracheque/${id}/assinar/`, { signature: assinatura });
    return data;
  },
};
