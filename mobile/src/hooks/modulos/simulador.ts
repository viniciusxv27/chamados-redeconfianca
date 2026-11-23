/** Hooks do módulo Simulador (TanStack Query sobre src/services/modulos/simulador.ts). */
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { simulador, type FiltroSimulador } from '@/services/modulos/simulador';

export const chavesSimulador = {
  resumo: (f: FiltroSimulador) => ['m', 'simulador', 'resumo', f] as const,
  pessoas: ['m', 'simulador', 'pessoas'] as const,
  tabela: ['m', 'simulador', 'tabela'] as const,
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

/** A tabela da rede vem do cache do dia do portal; enquanto calcula, pergunta de novo a cada 20 s. */
export function useTabelaSimulador() {
  return useQuery({
    queryKey: chavesSimulador.tabela,
    queryFn: simulador.tabela,
    staleTime: 5 * 60_000,
    refetchInterval: (q) => (q.state.data?.status === 'calculando' ? 20_000 : false),
  });
}
