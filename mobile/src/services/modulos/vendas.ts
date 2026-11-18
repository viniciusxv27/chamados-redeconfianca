/** Módulo Vendas (/api/mobile/v1/m/vendas/): resumo, vendas, tabela de preços, clientes e orçamento. */
import { api } from '../api';
import type { Pagina } from '../tipos';

export type Periodo = 'hoje' | 'mes' | 'todos';
export type Escopo = 'minhas' | 'loja' | 'rede';

export type PodeVendas = { rede: boolean; clientes: boolean; lancar: boolean; orcamentos: boolean; superadmin: boolean };

export type ResumoVendas = {
  pode: PodeVendas;
  loja: string | null;
  periodo: Periodo;
  escopo: Escopo;
  tabela_atualizada_em: string | null;
  indicadores: {
    total: number;
    quantidade: number;
    ticket: number;
    pecas: number;
    produtos: number;
    servicos: number;
    hoje: number;
    no_mes: number;
    fake: number;
    delta: number;
  };
  serie: { dia: string; n: number }[];
  top_produtos: { nome: string; n: number; total: number }[];
  por_tipo_servico: { tipo: string; n: number; delta: number }[];
  por_loja?: { nome: string; n: number }[];
  por_vendedor?: { nome: string; n: number }[];
};

export type VendaResumo = {
  id: number;
  data: string;
  cliente: string;
  loja: string | null;
  vendedor: string | null;
  total: number;
  total_servicos: number | null;
  forma_pagamento: string | null;
  tipo_venda: string | null;
  itens: string[];
  mais_itens: number;
  numero_fake: boolean;
};

export type Venda = VendaResumo & {
  cliente_cpf: string | null;
  cliente_telefone: string | null;
  lancada_por: string | null;
  comprovante: string;
  desconto_pix: number | null;
  vivo_mais: boolean;
  vivo_mais_percentual: number | null;
  plano_anterior: string | null;
  observacao: string | null;
  produtos: {
    id: number;
    nome: string;
    sku: string | null;
    cor: string | null;
    serial: string | null;
    qtde: number;
    valor: number;
    valor_sugerido: number | null;
    regra: string | null;
    valor_editado: boolean;
    prateleira_infinita: boolean;
    renova: string[];
  }[];
  servicos: {
    id: number;
    servico: string;
    plano: string | null;
    valor: number;
    plano_anterior: string | null;
    valor_anterior: number | null;
    delta: number | null;
    linha: string | null;
    status: string | null;
    portabilidade: boolean;
    numero_fake: boolean;
  }[];
  pagamentos: { forma: string; valor: number; parcelas: number | null }[];
  historico: { quando: string; por: string; motivo: string; linhas: { campo: string; item: string; antes: string; depois: string }[] }[] | null;
  url_web: string;
};

export type ProdutoResumo = {
  id: number;
  nome: string;
  categoria: string;
  marca: string | null;
  tipo: string | null;
  a_partir: number | null;
  pix: number | null;
  cores: number;
  sku_digitado: { sku: string; cor: string } | null;
};

export type Produto = ProdutoResumo & {
  cod_sap: string | null;
  sku: string | null;
  cor: string | null;
  skus: { sku: string; cor: string }[];
  valor: number | null;
  plano: string | null;
  grupamento: string | null;
  portfolio: string | null;
  categoria_slv: 'aparelho' | 'eletronico' | 'essencial';
  exige_serial: boolean;
  colunas: { chave: string; rotulo: string }[];
  linhas: { grupo: string; celulas: { valor: number | null; menor: boolean }[] }[];
  atualizado_em: string | null;
};

export type PaginaPrecos = Pagina<ProdutoResumo> & { abas: { valor: string; rotulo: string; n: number }[] };

export type ClienteResumo = {
  cpf: string;
  cpf_formatado: string;
  nome: string;
  telefone: string | null;
  ultima_compra: string | null;
  qtd_vendas: number;
  total_gasto: number;
  pdv_ultimo: string | null;
  origem: 'PORTAL' | 'VIVOGO';
};

export type Cliente = ClienteResumo & {
  linha: string | null;
  plano: string | null;
  endereco: string | null;
  primeira_compra: string | null;
  vendas_portal: (VendaResumo & { pode_abrir: boolean })[];
  compras_vivogo: {
    id: string | null;
    data: string | null;
    pdv: string | null;
    vendedor: string | null;
    itens: { item: string; tipo: string; qtde: number; valor: number | null }[];
  }[];
};

export type SituacaoCliente = '' | 'ativos' | 'novos' | 'recorrentes' | 'sumidos' | 'portal';

export type Orcamento = {
  id: number;
  cliente: string;
  pilar: string;
  pilar_nome: string;
  numero: string;
  texto: string;
  mensagem: string;
  status: 'ABERTO' | 'ENVIANDO' | 'ENVIADO' | 'FALHA';
  status_nome: string;
  criado_em: string;
  loja: string | null;
  link_whatsapp: string;
};

export type PaginaOrcamentos = Pagina<Orcamento> & {
  modelo: string;
  pilares: { valor: string; rotulo: string }[];
  limite: number;
  hoje: number;
};

export type NovoOrcamento = { cliente_nome: string; pilar: string; numero: string; texto: string };

export type FiltroVendas = { periodo: Periodo; escopo?: Escopo; busca?: string };

const BASE = '/m/vendas';

export const vendas = {
  async resumo(f: FiltroVendas) {
    const { data } = await api.get<ResumoVendas>(`${BASE}/`, { params: f });
    return data;
  },
  async listar(f: FiltroVendas, pagina: number) {
    const { data } = await api.get<Pagina<VendaResumo> & { periodo: Periodo; escopo: Escopo; rede: boolean }>(`${BASE}/vendas/`, { params: { ...f, pagina } });
    return data;
  },
  async detalhe(id: number) {
    const { data } = await api.get<Venda>(`${BASE}/vendas/${id}/`);
    return data;
  },
  async precos(f: { busca?: string; categoria?: string }, pagina: number) {
    const { data } = await api.get<PaginaPrecos>(`${BASE}/precos/`, { params: { ...f, pagina } });
    return data;
  },
  async produto(id: number) {
    const { data } = await api.get<Produto>(`${BASE}/precos/${id}/`);
    return data;
  },
  async clientes(f: { busca?: string; situacao?: SituacaoCliente }, pagina: number) {
    const { data } = await api.get<Pagina<ClienteResumo>>(`${BASE}/clientes/`, { params: { ...f, pagina } });
    return data;
  },
  async cliente(cpf: string) {
    const { data } = await api.get<Cliente>(`${BASE}/clientes/${encodeURIComponent(cpf)}/`);
    return data;
  },
  async orcamentos(busca: string | undefined, pagina: number) {
    const { data } = await api.get<PaginaOrcamentos>(`${BASE}/orcamentos/`, { params: { busca, pagina } });
    return data;
  },
  async criarOrcamento(o: NovoOrcamento) {
    const { data } = await api.post<Orcamento>(`${BASE}/orcamentos/`, o);
    return data;
  },
};

// ── Lançar venda (/m/vendas/lancar/…: o mesmo caminho da tela /vendas/nova/) ──

export type Opcao = { valor: string; rotulo: string };
export type PessoaOpcao = { id: number; nome: string; detalhe: string | null };

export type CamposServico = {
  plano?: string[];
  plano_anterior?: string[];
  sem_plano?: boolean;
  plano_fora?: boolean;
  portabilidade?: boolean;
  serial?: boolean;
  adicional?: boolean;
  titular?: boolean;
  numero_novo?: boolean;
};

export type FormularioVenda = {
  pode_outros: boolean;
  sem_pdv: boolean;
  loja: { id: number; nome: string } | null;
  eu: PessoaOpcao;
  lojas: { id: number; nome: string }[];
  vendedores: PessoaOpcao[];
  planos: { id: number; nome: string; segmentacao: string; valor: number }[];
  adicionais: { id: number; tipo: string; nome: string; valor: number }[];
  segmentacoes: Opcao[];
  tipos_servico: (Opcao & { campos: CamposServico })[];
  operadoras: Opcao[];
  formas: (Opcao & { nsu: boolean; parcelas: boolean })[];
  vivo_mais: number;
  cond_base: string;
  cond_pix: string;
  rotulos_condicao: Record<string, string>;
};

export type ClienteBusca =
  | { ok: false; erro: string }
  | { ok: true; encontrado: false; cpf: string; cpf_formatado: string }
  | {
      ok: true;
      encontrado: true;
      cliente: {
        cpf: string;
        cpf_formatado: string;
        nome: string;
        telefone: string;
        telefone_formatado: string;
        cep: string;
        logradouro: string;
        numero: string;
        complemento: string;
        bairro: string;
        cidade: string;
        uf: string;
        plano_id: number | null;
        plano_nome: string;
        segmentacao: string;
        valor_pago: string;
        origem: 'PORTAL' | 'VIVOGO';
        ultima_compra: string;
        pdv_ultimo: string;
        qtd_vendas: number;
      };
      pre_analise: {
        vazio: boolean;
        plano: { nome: string; segmentacao: string; segmentacao_nome: string; valor: string; fonte: string } | null;
        compras: { data: string; produto: string; valor: string; venda: number | null; pdv: string }[];
      };
    };

export type ProdutoVenda = {
  id: number;
  categoria: string;
  nome: string;
  sku: string;
  cor: string;
  skus: { sku: string; cor: string }[];
  sku_digitado: boolean;
  valor: string;
  categoria_slv: 'aparelho' | 'eletronico' | 'essencial';
  valor_sugerido: string;
  regra: string;
  fabricante: string;
  portfolio: string;
  tipo: string;
  exige_serial: boolean;
  tabela: Record<string, Record<string, string>>;
  grupos: string[];
  grupo_padrao: string;
  condicoes: Record<string, string[]>;
};

export type PlanoFora = { id: number; nome: string; valor: string; segmentacao: string };

export type ErroDaVenda = { erro: string; erros: string[]; etapas: Partial<Record<'cliente' | 'produtos' | 'servicos' | 'pagamento', string[]>> };

const LANCAR = `${BASE}/lancar`;

export const lancar = {
  async formulario() {
    const { data } = await api.get<FormularioVenda>(`${LANCAR}/`);
    return data;
  },
  async cliente(cpf: string) {
    const { data } = await api.get<ClienteBusca>(`${LANCAR}/cliente/`, { params: { cpf } });
    return data;
  },
  async produtos(q: string, segmentacao: string) {
    const { data } = await api.get<{ results: ProdutoVenda[] }>(`${LANCAR}/produtos/`, { params: { q, segmentacao } });
    return data.results;
  },
  /** O preço sugerido de novo para o plano do cliente (a web chama assim quando o plano muda). */
  async produtosPorId(ids: number[], segmentacao: string) {
    const { data } = await api.get<{ results: ProdutoVenda[] }>(`${LANCAR}/produtos/`, { params: { ids: ids.join(','), segmentacao } });
    return data.results;
  },
  async renova(codigo: string) {
    const { data } = await api.get<{ ok: boolean; codigo?: string; aparelho?: string; ja_usado?: boolean; erro?: string }>(`${LANCAR}/renova/`, { params: { codigo } });
    return data;
  },
  async planosFora(q: string) {
    const { data } = await api.get<{ ok: boolean; itens: PlanoFora[] }>(`${LANCAR}/planos-fora/`, { params: { q } });
    return data.itens ?? [];
  },
  /** 201 → a venda; 400 → os erros por etapa (o ErroApi comum guarda só a mensagem, então o 400 vem como resposta). */
  async gravar(payload: object): Promise<{ ok: true; id: number; url_web: string } | ({ ok: false } & ErroDaVenda)> {
    const r = await api.post(`${LANCAR}/`, payload, { validateStatus: (s) => s === 201 || s === 400 });
    return r.status === 201 ? r.data : { ok: false, ...(r.data as ErroDaVenda) };
  },
};
