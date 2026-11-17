/** Hooks do módulo Contracheque (TanStack Query sobre src/services/modulos/contracheque.ts). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { contracheque, type HoleriteDetalhe } from '@/services/modulos/contracheque';

export const chavesContracheque = {
  inicio: ['m', 'contracheque', 'inicio'] as const,
  detalhe: (id: number) => ['m', 'contracheque', 'detalhe', id] as const,
  informe: (id: number) => ['m', 'contracheque', 'informe', id] as const,
};

export function useContracheques() {
  return useQuery({ queryKey: chavesContracheque.inicio, queryFn: contracheque.inicio });
}

/** O detalhe traz links de PDF que valem 5 min: refaz a busca depois de 4 min parado. */
export function useContracheque(id: number) {
  return useQuery({ queryKey: chavesContracheque.detalhe(id), queryFn: () => contracheque.detalhe(id), enabled: id > 0, staleTime: 4 * 60_000 });
}

export function useInforme(id: number) {
  return useQuery({ queryKey: chavesContracheque.informe(id), queryFn: () => contracheque.informe(id), enabled: id > 0, staleTime: 4 * 60_000 });
}

export function useAssinarContracheque(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (assinatura: string) => contracheque.assinar(id, assinatura),
    onSuccess: (detalhe: HoleriteDetalhe) => {
      cliente.setQueryData(chavesContracheque.detalhe(id), detalhe);
      void cliente.invalidateQueries({ queryKey: chavesContracheque.inicio });
    },
  });
}
