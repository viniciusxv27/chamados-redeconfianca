/** Hooks do módulo Simulador (TanStack Query sobre src/services/modulos/simulador.ts). */
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { simulador, type FiltroSimulador } from '@/services/modulos/simulador';

export const chavesSimulador = {
  resumo: (f: FiltroSimulador) => ['m', 'simulador', 'resumo', f] as const,
  pessoas: ['m', 'simulador', 'pessoas'] as const,
};

/** O realizado do mês fica 15 min em cache no portal; 3 min sem buscar de novo aqui. */
export function useSimulador(filtro: FiltroSimulador, ligado = true) {
  return useQuery({
    queryKey: chavesSimulador.resumo(filtro),
    queryFn: () => simulador.resumo(filtro),
    enabled: ligado,
    staleTime: 3 * 60_000,
    placeholderData: keepPreviousData,
  });
}

export function usePessoasSimulador(ligado: boolean) {
  return useQuery({ queryKey: chavesSimulador.pessoas, queryFn: simulador.pessoas, enabled: ligado, staleTime: 10 * 60_000 });
}
