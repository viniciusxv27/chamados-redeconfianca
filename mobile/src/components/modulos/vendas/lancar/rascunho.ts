/**
 * O rascunho da venda (em memória, sobrevive à troca de etapa e a sair da tela) e as contas da
 * tela /vendas/nova/ do portal, na mesma ordem (templates/vendas/venda_form.html): preço pela
 * tabela, Renova, Vivo+, desconto no Pix, pagamentos. O servidor refaz tudo e decide
 * (vendas/slv.py:registrar_venda); aqui é só para a pessoa ver e corrigir antes de enviar.
 */
import { useSyncExternalStore } from 'react';

import type { ClienteBusca, PlanoFora, ProdutoVenda } from '@/services/modulos/vendas';

export const COND_BASE = 'Preço';
export const COND_PIX = 'PIX e Vivo Pay';
export const ETAPAS = ['Cliente', 'Itens', 'Pagamento', 'Revisão'] as const;

export type DadosCliente = {
  nome: string;
  telefone: string;
  cep: string;
  logradouro: string;
  numero: string;
  complemento: string;
  bairro: string;
  cidade: string;
  uf: string;
};

export type ProdutoRascunho = {
  chave: string;
  preco_id: number;
  nome: string;
  categoria: string;
  categoria_slv: ProdutoVenda['categoria_slv'];
  tipo: string;
  sku: string;
  skus: { sku: string; cor: string }[];
  exige_serial: boolean;
  tabela: Record<string, Record<string, string>> | null;
  grupos: string[];
  condicoes: Record<string, string[]>;
  grupo: string;
  condicao: string;
  valor_sugerido: number | null;
  sem_valor: boolean;
  valor_informado: string;
  regra: string;
  imei: string;
  prateleira_infinita: boolean;
  renova: boolean;
  renova_vini: boolean;
  renova_vini_valor: string;
  renova_codigo: string;
  renova_info: string | null;
  renova_ok: boolean | null;
  renova_alied: boolean;
  renova_alied_valor: string;
  renova_alied_pedido: string;
  renova_assurant: boolean;
  renova_assurant_valor: string;
  renova_assurant_pedido: string;
  valor_final: string;
};

export type ServicoRascunho = {
  chave: string;
  tipo: string;
  rotulo: string;
  descricao: string;
  valor: number;
  delta: number | null;
  plano_id: number | null;
  plano_anterior_id: number | null;
  plano_fora_id: number | null;
  plano_fora: PlanoFora | null;
  sem_plano: boolean;
  serial: string;
  servico_adicional_id: number | null;
  ativacao_confirmada: boolean;
  novo_titular_cpf: string;
  novo_titular_nome: string;
  numero_novo: string;
  numero_ordem: string;
  portabilidade: boolean;
  operadora_origem: string;
};

export type Pagamento = { forma: string; valor: string; nsu: string; parcelas: string };

export type PreAnalise = Extract<ClienteBusca, { encontrado: true }>['pre_analise'];

export type Rascunho = {
  etapa: number;
  loja_id: number | null;
  vendedor_id: number | null;
  cpf: string;
  cpf_formatado: string;
  encontrado: boolean | null;
  confirmado: boolean;
  origem: string;
  pre: PreAnalise | null;
  cliente: DadosCliente;
  plano_anterior: { id: number | null; segmentacao: string; valor: string };
  produtos: ProdutoRascunho[];
  servicos: ServicoRascunho[];
  linhas: string[];
  vivo_mais: boolean;
  desconto_pix: string;
  pagamentos: Pagamento[];
  observacao: string;
};

export const CLIENTE_VAZIO: DadosCliente = { nome: '', telefone: '', cep: '', logradouro: '', numero: '', complemento: '', bairro: '', cidade: '', uf: '' };

function inicial(): Rascunho {
  return {
    etapa: 0,
    loja_id: null,
    vendedor_id: null,
    cpf: '',
    cpf_formatado: '',
    encontrado: null,
    confirmado: false,
    origem: '',
    pre: null,
    cliente: CLIENTE_VAZIO,
    plano_anterior: { id: null, segmentacao: '', valor: '' },
    produtos: [],
    servicos: [],
    linhas: [],
    vivo_mais: false,
    desconto_pix: '',
    pagamentos: [{ forma: '', valor: '', nsu: '', parcelas: '' }],
    observacao: '',
  };
}

// ── O rascunho em memória ────────────────────────────────────────────────────
let atual = inicial();
const ouvintes = new Set<() => void>();

export function mudarRascunho(fn: (r: Rascunho) => Rascunho) {
  atual = fn(atual);
  ouvintes.forEach((o) => o());
}

export function descartarRascunho() {
  mudarRascunho(() => inicial());
}

export function useRascunho() {
  return useSyncExternalStore(
    (o) => {
      ouvintes.add(o);
      return () => ouvintes.delete(o);
    },
    () => atual,
  );
}

export function temRascunho(r: Rascunho) {
  return !!(r.cpf || r.produtos.length || r.servicos.length);
}

let sequencia = 0;
export const novaChave = () => `i${Date.now()}${(sequencia += 1)}`;

// ── Formatos e conferências (as mesmas da tela da web) ───────────────────────
export const so = (v: string | null | undefined) => String(v ?? '').replace(/\D/g, '');

/** "1.234,56", "1234.56" → 1234.56; vazio/inválido → null. */
export function num(v: string | number | null | undefined) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return Number.isNaN(v) ? null : Math.round(v * 100) / 100;
  let t = String(v).replace(/R\$|\s/g, '');
  if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.');
  const n = Number(t);
  return Number.isNaN(n) ? null : Math.round(n * 100) / 100;
}

export const moeda = (n: number | null | undefined) => (n === null || n === undefined ? '' : n.toFixed(2).replace('.', ','));

export function cpfValido(v: string) {
  const d = so(v);
  if (d.length !== 11 || /^(\d)\1{10}$/.test(d)) return false;
  for (const t of [9, 10]) {
    let s = 0;
    for (let i = 0; i < t; i++) s += Number(d[i]) * (t + 1 - i);
    if (((s * 10) % 11) % 10 !== Number(d[t])) return false;
  }
  return true;
}

export function cnpjValido(v: string) {
  const d = so(v);
  if (d.length !== 14 || /^(\d)\1{13}$/.test(d)) return false;
  for (const t of [12, 13]) {
    const pesos: number[] = [];
    for (let p = t - 7; p > 1; p--) pesos.push(p);
    for (let p = 9; p > 1; p--) pesos.push(p);
    let s = 0;
    for (let i = 0; i < t; i++) s += Number(d[i]) * pesos[i];
    const r = s % 11;
    if ((r < 2 ? 0 : 11 - r) !== Number(d[t])) return false;
  }
  return true;
}

export function mascaraDoc(v: string) {
  const d = so(v).slice(0, 14);
  if (d.length <= 11)
    return d
      .replace(/^(\d{3})(\d)/, '$1.$2')
      .replace(/^(\d{3})\.(\d{3})(\d)/, '$1.$2.$3')
      .replace(/\.(\d{3})(\d)/, '.$1-$2');
  return d
    .replace(/^(\d{2})(\d)/, '$1.$2')
    .replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3')
    .replace(/\.(\d{3})(\d)/, '.$1/$2')
    .replace(/(\d{4})(\d)/, '$1-$2');
}

export function mascaraTel(v: string) {
  const d = so(v).slice(0, 11);
  if (d.length <= 2) return d;
  if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
  if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
}

export function telValido(v: string) {
  const d = so(v);
  return (d.length === 10 || d.length === 11) && Number(d.slice(0, 2)) >= 11;
}

/** Número fictício (999999999, repetido…): a venda fica marcada para conferência. */
export function fake(v: string) {
  const d = so(v);
  if (!d) return false;
  if (d === '999999999' || d === '9000000000' || d.length < 8) return true;
  const corpo = d.length === 10 || d.length === 11 ? d.slice(2) : d;
  return new Set(corpo).size === 1 || /^\d0+$/.test(corpo);
}

/** 15 números e o dígito final (Luhn) — o mesmo teste do servidor. */
export function imeiValido(v: string) {
  if (!/^\d{15}$/.test(v || '')) return false;
  let soma = 0;
  for (let i = 0; i < 14; i++) {
    let d = Number(v[i]);
    if (i % 2) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    soma += d;
  }
  return (10 - (soma % 10)) % 10 === Number(v[14]);
}

export function rotuloCondicao(c: string) {
  return c === COND_BASE ? 'Preço (cartão sem juros)' : c;
}

// ── Produto ──────────────────────────────────────────────────────────────────
/** Grupo do plano + condição → valor da TABELA REGULAR (o mesmo cálculo do servidor). */
export function aplicarTabela(p: ProdutoRascunho, seg: string): ProdutoRascunho {
  if (!p.tabela) return p;
  let grupo = p.grupo;
  if (!grupo && (seg === 'PRE' || !seg) && p.grupos[0] && /^PR/i.test(p.grupos[0])) grupo = p.grupos[0];
  const linha = grupo ? p.tabela[grupo] : null;
  const condicao = linha && !(p.condicao in linha) ? COND_BASE : p.condicao;
  const v = linha ? linha[condicao] : null;
  return {
    ...p,
    grupo,
    condicao,
    valor_sugerido: v ? num(v) : null,
    sem_valor: !v,
    regra: !grupo ? 'escolha o grupo do plano' : v ? `${grupo} · ${rotuloCondicao(condicao)}` : 'sem valor nessa condição',
    valor_final: '',
  };
}

export function novoProduto(r: ProdutoVenda, pagamentos: Pagamento[], seg: string): ProdutoRascunho {
  const comTabela = r.grupos && r.grupos.length ? r.tabela : null;
  const p: ProdutoRascunho = {
    chave: novaChave(),
    preco_id: r.id,
    nome: r.nome,
    categoria: r.categoria,
    categoria_slv: r.categoria_slv,
    tipo: r.tipo,
    sku: r.sku || '',
    skus: r.skus || [],
    exige_serial: !!r.exige_serial,
    tabela: comTabela,
    grupos: r.grupos || [],
    condicoes: r.condicoes || {},
    grupo: r.grupo_padrao || '',
    condicao: COND_BASE,
    valor_sugerido: num(r.valor_sugerido),
    sem_valor: !r.valor_sugerido,
    valor_informado: '',
    regra: r.regra,
    imei: '',
    prateleira_infinita: false,
    renova: false,
    renova_vini: false,
    renova_vini_valor: '',
    renova_codigo: '',
    renova_info: null,
    renova_ok: null,
    renova_alied: false,
    renova_alied_valor: '',
    renova_alied_pedido: '',
    renova_assurant: false,
    renova_assurant_valor: '',
    renova_assurant_pedido: '',
    valor_final: '',
  };
  return aplicarTabela({ ...p, condicao: condicaoPelaForma(p, pagamentos) }, seg);
}

/** O preço do aparelho segue o pagamento: tudo Pix/Vivo Pay → coluna do Pix; cartão → a coluna da maior parcela. */
function formaDosPagamentos(pagamentos: Pagamento[]) {
  const cartoes = pagamentos.filter((pg) => pg.forma === 'CARTAO');
  const escolhidas = pagamentos.map((pg) => pg.forma).filter(Boolean);
  const forma = cartoes.length ? 'CARTAO' : !escolhidas.length ? '' : escolhidas.every((f) => f === 'PIX' || f === 'VIVO_PAY') ? 'PIX' : 'OUTRA';
  const parc = cartoes.reduce((m, pg) => Math.max(m, Number(pg.parcelas) || 0), 0);
  return { forma, parc };
}

export function condicaoPelaForma(p: ProdutoRascunho, pagamentos: Pagamento[]) {
  if (!p.tabela) return p.condicao;
  const { forma, parc } = formaDosPagamentos(pagamentos);
  const parcelada = /^\d+x$/.test(p.condicao);
  if (forma === 'PIX') return p.condicao === COND_BASE || parcelada ? COND_PIX : p.condicao;
  if (forma === 'OUTRA') return p.condicao === COND_PIX || parcelada ? COND_BASE : p.condicao;
  if (forma !== 'CARTAO' || !p.grupo) return p.condicao;
  if (!parc) return p.condicao === COND_PIX ? COND_BASE : p.condicao;
  const conds = p.condicoes[p.grupo] || [];
  return parc > 1 && conds.includes(`${parc}x`) ? `${parc}x` : COND_BASE;
}

/** Trocou o pagamento: a condição dos aparelhos da tabela acompanha (como na web). */
export function comPagamentos(r: Rascunho, pagamentos: Pagamento[]): Rascunho {
  const antes = formaDosPagamentos(r.pagamentos);
  const depois = formaDosPagamentos(pagamentos);
  if (antes.forma === depois.forma && antes.parc === depois.parc) return { ...r, pagamentos };
  return {
    ...r,
    pagamentos,
    produtos: r.produtos.map((p) => {
      const c = condicaoPelaForma(p, pagamentos);
      return c === p.condicao ? p : aplicarTabela({ ...p, condicao: c }, r.plano_anterior.segmentacao);
    }),
  };
}

export function sugeridoDe(p: ProdutoRascunho) {
  return p.sem_valor ? num(p.valor_informado) : p.valor_sugerido;
}

/** valor do produto − Renova − Vivo+ (vendas/slv.py:calcular_item). */
export function calcItem(p: ProdutoRascunho, vivoMais: boolean, pct: number) {
  const sug = sugeridoDe(p) ?? 0;
  const rv = p.renova && p.renova_vini ? (num(p.renova_vini_valor) ?? 0) : 0;
  const ra = p.renova && p.renova_alied ? (num(p.renova_alied_valor) ?? 0) : 0;
  const ras = p.renova && p.renova_assurant ? (num(p.renova_assurant_valor) ?? 0) : 0;
  const base = sug - rv - ra - ras;
  const desc = vivoMais ? Math.round(Math.max(base, 0) * pct) / 100 : 0;
  const calc = Math.round((base - desc) * 100) / 100;
  const digitado = num(p.valor_final);
  const final = digitado ?? calc;
  return { sug, rv, ra, ras, desc, calc, final, editado: digitado !== null && digitado !== calc };
}

// ── Totais e pagamento ───────────────────────────────────────────────────────
const centavos = (v: number | null) => Math.round((v ?? 0) * 100);

export function temPix(r: Rascunho) {
  return r.pagamentos.some((pg) => pg.forma === 'PIX');
}

export function totais(r: Rascunho, pct: number) {
  let produtos = 0;
  let descontos = 0;
  r.produtos.forEach((p) => {
    const c = calcItem(p, r.vivo_mais, pct);
    produtos += c.final;
    descontos += c.rv + c.ra + c.ras + c.desc;
  });
  const servicos = r.servicos.reduce((t, s) => t + s.valor, 0);
  const descontoPix = temPix(r) ? (num(r.desconto_pix) ?? 0) : 0;
  const total = Math.round((produtos - descontoPix) * 100) / 100;
  return { produtos, descontos, servicos, descontoPix, total };
}

/** O que falta: a última parte sem valor fica com o restante (como na web). */
export function restante(r: Rascunho, total: number) {
  const fixos = r.pagamentos.slice(0, -1).reduce((t, pg) => t + centavos(num(pg.valor)), 0);
  return (centavos(total) - fixos) / 100;
}

export function valorDoPagamento(r: Rascunho, i: number, total: number) {
  const pg = r.pagamentos[i];
  return pg.valor === '' && i === r.pagamentos.length - 1 ? restante(r, total) : num(pg.valor);
}

// ── Conferência por etapa (espelha a tela; o servidor decide) ────────────────
export function errosCliente(r: Rascunho, semPdv: boolean) {
  const e: string[] = [];
  if (semPdv) e.push('Seu usuário não tem PDV vinculado: procure o administrador do portal.');
  const d = so(r.cpf);
  if (!(d.length === 14 ? cnpjValido(d) : cpfValido(d))) e.push('CPF/CNPJ do cliente inválido.');
  else if (!r.confirmado) e.push(r.encontrado ? 'Confirme se é o cliente certo.' : 'Busque o cliente pelo CPF/CNPJ.');
  if (!r.cliente.nome.trim()) e.push('Informe o nome do cliente.');
  if (r.cliente.telefone && !telValido(r.cliente.telefone) && !fake(r.cliente.telefone)) e.push('Telefone: use DDD + número.');
  return e;
}

export function errosItens(r: Rascunho, pct: number) {
  const e: string[] = [];
  if (!r.produtos.length && !r.servicos.length) e.push('Adicione ao menos um produto ou serviço.');
  r.produtos.forEach((p) => {
    if (p.categoria_slv === 'aparelho') {
      if (p.prateleira_infinita && !p.imei) {
        // o IMEI fica para depois
      } else if (!imeiValido(p.imei)) e.push(`${p.nome}: informe o IMEI — 15 números (confira o dígito final) ou marque Prateleira infinita.`);
      else if (r.produtos.filter((o) => o.imei === p.imei).length > 1) e.push(`${p.nome}: o IMEI ${p.imei} está repetido nesta venda.`);
    } else if (p.exige_serial && !p.imei && !p.prateleira_infinita) e.push(`${p.nome}: informe o serial ou marque Prateleira infinita.`);
    if (p.skus.length > 1 && !p.sku) e.push(`${p.nome}: escolha a cor (SKU).`);
    if (p.tabela && !p.grupo) e.push(`${p.nome}: escolha o grupo do plano.`);
    if (p.sem_valor && !((num(p.valor_informado) ?? 0) > 0)) e.push(`${p.nome}: sem valor na tabela para este plano — informe o valor.`);
    if (p.renova && p.renova_vini) {
      if (!so(p.renova_codigo)) e.push(`${p.nome}: informe o código do Renova Vini (RN-000000).`);
      if (!((num(p.renova_vini_valor) ?? 0) > 0)) e.push(`${p.nome}: informe o valor do Renova Vini.`);
    }
    if (p.renova && p.renova_alied) {
      if (!((num(p.renova_alied_valor) ?? 0) > 0)) e.push(`${p.nome}: informe o valor do Renova Alied.`);
      if (!p.renova_alied_pedido.trim()) e.push(`${p.nome}: informe o número do pedido do Renova Alied.`);
    }
    if (p.renova && p.renova_assurant && !((num(p.renova_assurant_valor) ?? 0) > 0)) e.push(`${p.nome}: informe o valor do Renova Assurant.`);
    if (calcItem(p, r.vivo_mais, pct).final <= 0 && !p.sem_valor) e.push(`${p.nome}: o valor final ficou zerado ou negativo.`);
  });
  if (r.servicos.length && !r.linhas.length) e.push('Informe a linha do cliente para o serviço.');
  return e;
}

export function errosPagamento(r: Rascunho, pct: number) {
  const e: string[] = [];
  if (!r.produtos.length) return e;
  const { total, descontoPix, produtos } = totais(r, pct);
  const nsus = new Set<string>();
  r.pagamentos.forEach((pg, i) => {
    const rotulo = r.pagamentos.length > 1 ? `Pagamento ${i + 1}` : 'Pagamento';
    if (!pg.forma) return void e.push(`${rotulo}: escolha a forma de pagamento.`);
    const v = valorDoPagamento(r, i, total);
    if (!v || v <= 0) e.push(`${rotulo}: informe o valor.`);
    if (pg.forma === 'CARTAO' || pg.forma === 'DEBITO') {
      const nsu = pg.nsu.replace(/[^0-9A-Za-z]/g, '').toUpperCase();
      if (nsu.length < 4) e.push(`${rotulo}: informe o NSU do comprovante da maquininha.`);
      else if (nsus.has(nsu)) e.push(`${rotulo}: o NSU ${nsu} já está em outro cartão desta venda.`);
      nsus.add(nsu);
    }
    if (pg.forma === 'CARTAO') {
      const n = Number(pg.parcelas);
      if (!(n >= 1 && n <= 24)) e.push(`${rotulo}: informe em quantas parcelas (1 a 24).`);
    }
  });
  if (descontoPix && descontoPix >= produtos) e.push('O desconto no Pix não pode chegar ao total da venda.');
  const pago = r.pagamentos.reduce((t, _pg, i) => t + centavos(valorDoPagamento(r, i, total)), 0);
  if (r.pagamentos.every((pg) => pg.forma) && pago !== centavos(total)) {
    const dif = (centavos(total) - pago) / 100;
    e.push(dif > 0 ? `Falta R$ ${moeda(dif)} nos pagamentos.` : `Os pagamentos passam do total em R$ ${moeda(-dif)}.`);
  }
  return e;
}

// ── O JSON da tela (o mesmo que a web manda para /vendas/nova/) ───────────────
export function montarPayload(r: Rascunho, pct: number, outros: boolean) {
  const { total } = totais(r, pct);
  return {
    cliente: { cpf: so(r.cpf), ...Object.fromEntries(Object.entries(r.cliente).map(([k, v]) => [k, v.trim()])) },
    linhas: r.linhas,
    observacao: r.observacao,
    vivo_mais: r.vivo_mais,
    desconto_pix: temPix(r) ? r.desconto_pix : '',
    pagamentos: r.produtos.length
      ? r.pagamentos.map((pg, i) => ({
          forma: pg.forma,
          valor: (valorDoPagamento(r, i, total) ?? 0).toFixed(2),
          nsu: pg.forma === 'CARTAO' || pg.forma === 'DEBITO' ? pg.nsu : '',
          parcelas: pg.forma === 'CARTAO' ? pg.parcelas : '',
        }))
      : [],
    plano_anterior: { id: r.plano_anterior.id ?? '', segmentacao: r.plano_anterior.segmentacao, valor: r.plano_anterior.valor, nome: '' },
    produtos: r.produtos.map((p) => {
      const c = calcItem(p, r.vivo_mais, pct);
      return {
        preco_id: p.preco_id,
        nome: p.nome,
        sku: p.sku,
        imei: p.imei,
        prateleira_infinita: p.prateleira_infinita,
        grupamento: p.grupo,
        condicao: p.condicao,
        valor_sugerido: sugeridoDe(p),
        renova_vini: p.renova && p.renova_vini,
        renova_vini_valor: p.renova_vini_valor,
        renova_codigo: p.renova_codigo,
        renova_alied: p.renova && p.renova_alied,
        renova_alied_valor: p.renova_alied_valor,
        renova_alied_pedido: p.renova_alied_pedido,
        renova_assurant: p.renova && p.renova_assurant,
        renova_assurant_valor: p.renova_assurant_valor,
        renova_assurant_pedido: p.renova_assurant_pedido,
        valor_final: c.editado ? c.final : null,
      };
    }),
    servicos: r.servicos.map(({ chave: _c, rotulo: _r, descricao: _d, valor: _v, delta: _dl, plano_fora: _pf, ...s }) => s),
    ...(outros ? { loja_id: r.loja_id, vendedor_id: r.vendedor_id } : {}),
  };
}
