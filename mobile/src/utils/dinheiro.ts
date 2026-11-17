/** Valores em reais e percentuais no formato brasileiro. */
const REAL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

export function reais(valor: number | string | null | undefined) {
  const n = typeof valor === 'string' ? Number(valor) : valor;
  return n == null || Number.isNaN(n) ? '—' : REAL.format(n);
}

/** 0.873 → "87,3%" (fração); use `porcentagem(87.3, false)` se já vier em %. */
export function porcentagem(valor: number | null | undefined, fracao = true, casas = 1) {
  if (valor == null || Number.isNaN(valor)) return '—';
  const v = fracao ? valor * 100 : valor;
  return `${v.toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas })}%`;
}

export function numero(valor: number | null | undefined, casas = 0) {
  if (valor == null || Number.isNaN(valor)) return '—';
  return valor.toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
}
