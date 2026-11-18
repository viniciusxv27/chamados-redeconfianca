/** Hooks do módulo Power BI (TanStack Query sobre src/services/modulos/power_bi.ts). */
import { useMutation, useQuery } from '@tanstack/react-query';

import { powerBi } from '@/services/modulos/power_bi';

export const chavesPowerBi = {
  lista: ['m', 'power_bi', 'lista'] as const,
};

/** A lista muda pouco (cadastro do SUPERADMIN): 5 min sem buscar de novo. */
export function useDashboards() {
  return useQuery({ queryKey: chavesPowerBi.lista, queryFn: powerBi.listar, staleTime: 5 * 60_000 });
}

export function useAbrirDashboard() {
  return useMutation({ mutationFn: (id: number) => powerBi.abrir(id) });
}
