/** Módulo Mercadinho (/api/mobile/v1/m/mercadinho/): vitrine de prêmios, resgate com C$ e meus resgates. */
import { api } from '../api';
import type { Pagina } from '../tipos';

export type OrdemVitrine = 'relevancia' | 'menor_valor' | 'maior_valor' | 'recentes';
export type StatusResgate = 'PENDENTE' | 'APROVADO' | 'ENTREGUE' | 'CANCELADO';

export type CategoriaPremio = { id: number; nome: string };

export type PremioResumo = {
  id: number;
  nome: string;
  categoria: CategoriaPremio | null;
  imagem: string | null;
  /** Preço cheio em C$. */
  valor: number;
  /** O que sai do saldo (com desconto, se houver). */
  valor_final: number;
  desconto_percentual: number | null;
  destaque: boolean;
  /** null = estoque ilimitado. */
  restantes: number | null;
  disponivel: boolean;
  saldo_suficiente: boolean;
  valido_ate: string | null;
};

export type Premio = PremioResumo & {
  descricao: string | null;
  termos: string | null;
  saldo: number;
  caminho_web: string;
};

export type PaginaVitrine = Pagina<PremioResumo> & {
  /** Só na primeira página. */
  saldo?: number;
  categorias?: CategoriaPremio[];
  aguardando_retirada?: { id: number; premio: string; retirar_ate: string }[];
  pendentes?: number;
  pode_gerenciar?: boolean;
};

export type Resgate = {
  id: number;
  premio: { id: number; nome: string; imagem: string | null };
  status: StatusResgate;
  status_nome: string;
  valor: number;
  resgatado_em: string | null;
  aprovado_em: string | null;
  entregue_em: string | null;
  /** Data (AAAA-MM-DD) limite para retirar na SEDE. */
  retirar_ate: string | null;
  aguardando_retirada: boolean;
  observacao: string | null;
  pode_cancelar: boolean;
};

export type PaginaResgates = Pagina<Resgate> & { contagem?: Record<StatusResgate, number>; saldo?: number };

export type FiltroVitrine = { busca?: string; categoria?: number; ordem?: OrdemVitrine };

export const mercadinho = {
  async vitrine(f: FiltroVitrine, pagina: number) {
    const { data } = await api.get<PaginaVitrine>('/m/mercadinho/', { params: { ...f, pagina } });
    return data;
  },
  async premio(id: number) {
    const { data } = await api.get<Premio>(`/m/mercadinho/premios/${id}/`);
    return data;
  },
  async resgatar(id: number) {
    const { data } = await api.post<{ mensagem: string; saldo: number; resgate: Resgate | null }>(`/m/mercadinho/premios/${id}/resgatar/`);
    return data;
  },
  async resgates(situacao: StatusResgate | undefined, pagina: number) {
    const { data } = await api.get<PaginaResgates>('/m/mercadinho/resgates/', { params: { situacao, pagina } });
    return data;
  },
  async cancelar(id: number) {
    const { data } = await api.post<{ mensagem: string; saldo: number; resgate: Resgate }>(`/m/mercadinho/resgates/${id}/cancelar/`);
    return data;
  },
  async sugerir(corpo: { link: string; nome?: string; observacao?: string }) {
    const { data } = await api.post<{ mensagem: string }>('/m/mercadinho/sugerir/', corpo);
    return data;
  },
};
