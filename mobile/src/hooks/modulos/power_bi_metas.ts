/** Hooks do módulo Metas (TanStack Query sobre src/services/modulos/power_bi_metas.ts). */
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { type FiltroMetas, metas } from '@/services/modulos/power_bi_metas';

export const chavesMetas = {
  resumo: (f: FiltroMetas) => ['m', 'power_bi_metas', 'resumo', f] as const,
  cargas: ['m', 'power_bi_metas', 'cargas'] as const,
};

/** A planilha de metas sobe uma vez por mês: 5 min sem buscar de novo. */
export function useMetas(filtro: FiltroMetas) {
  return useQuery({ queryKey: chavesMetas.resumo(filtro), queryFn: () => metas.resumo(filtro), staleTime: 5 * 60_000, placeholderData: keepPreviousData });
}

export function useCargasMetas() {
  return useQuery({ queryKey: chavesMetas.cargas, queryFn: metas.cargas, staleTime: 5 * 60_000 });
}
