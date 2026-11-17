/** Rotas do Quadro de HC (loja leva junto os filtros de item da rede). */
import { router, type Href } from 'expo-router';

import type { FiltroQuadro } from '@/services/modulos/quadro_hc';

export function abrirLoja(id: number, filtro: FiltroQuadro) {
  const params: Record<string, string> = { id: String(id) };
  if (filtro.busca) params.busca = filtro.busca;
  if (filtro.cargo) params.cargo = filtro.cargo;
  if (filtro.rapido) params.rapido = filtro.rapido;
  router.push({ pathname: '/modulo/quadro_hc/[id]', params } as unknown as Href);
}

export function abrirPessoa(id: number) {
  router.push({ pathname: '/modulo/quadro_hc/pessoa/[id]', params: { id: String(id) } } as unknown as Href);
}

export function abrirAjuste(id: number) {
  router.push({ pathname: '/modulo/quadro_hc/ajuste/[id]', params: { id: String(id) } } as unknown as Href);
}

export function abrirPrevisto(id: number) {
  router.push({ pathname: '/modulo/quadro_hc/previsto/[id]', params: { id: String(id) } } as unknown as Href);
}
