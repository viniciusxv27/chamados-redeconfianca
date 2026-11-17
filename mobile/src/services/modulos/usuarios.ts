/** Módulo Usuários: a lista de colaboradores do portal (/users/manage/users/), só leitura. */
import { api } from '../api';
import type { Pagina } from '../tipos';

export type Hierarquia = 'PADRAO' | 'ADMINISTRATIVO' | 'SUPERVISOR' | 'ADMIN' | 'SUPERADMIN';
export type SituacaoUsuario = 'ATIVO' | 'INATIVO' | 'AFASTADO' | 'FERIAS';

export type UsuarioResumo = {
  id: number;
  nome: string;
  login: string;
  email: string;
  telefone: string | null;
  foto: string | null;
  cargo: string | null;
  setor: string | null;
  hierarquia: Hierarquia;
  hierarquia_nome: string;
  status: SituacaoUsuario;
  status_nome: string;
  /** false = não entra no portal (is_active desligado). */
  acesso_ativo: boolean;
  /** Só vem para quem a tela web mostra a coluna Saldo C$. */
  saldo_cs?: string;
};

export type UsuarioDetalhe = UsuarioResumo & {
  primeiro_nome: string;
  setor_principal: string | null;
  setores: string[];
  grupos: string[];
  admissao: string | null;
  /** 55 + DDD + número, pronto para wa.me (null se o telefone não parece do Brasil). */
  whatsapp: string | null;
  afastamento_retorno: string | null;
  ferias_inicio: string | null;
  ferias_fim: string | null;
  superadmin: boolean;
  url_web: string;
};

export type FiltrosUsuarios = {
  busca?: string;
  status?: SituacaoUsuario | '';
  setor?: number | null;
  hierarquia?: Hierarquia | '';
};

export type OpcoesUsuarios = {
  setores: { id: number; nome: string }[];
  hierarquias: { valor: Hierarquia; rotulo: string }[];
  situacoes: { valor: SituacaoUsuario; rotulo: string }[];
  totais: { usuarios: number; ativos: number; superadmins: number };
};

export const usuarios = {
  async listar(filtro: FiltrosUsuarios, pagina: number) {
    const params: Record<string, string | number> = { pagina };
    if (filtro.busca) params.busca = filtro.busca;
    if (filtro.status) params.status = filtro.status;
    if (filtro.setor) params.setor = filtro.setor;
    if (filtro.hierarquia) params.hierarquia = filtro.hierarquia;
    const { data } = await api.get<Pagina<UsuarioResumo>>('/m/usuarios/', { params });
    return data;
  },
  async opcoes() {
    const { data } = await api.get<OpcoesUsuarios>('/m/usuarios/filtros/');
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<UsuarioDetalhe>(`/m/usuarios/${id}/`);
    return data;
  },
};
