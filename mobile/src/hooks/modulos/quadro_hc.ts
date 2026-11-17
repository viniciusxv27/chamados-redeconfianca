/** Hooks do módulo Quadro de HC (TanStack Query sobre src/services/modulos/quadro_hc.ts). */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type FiltroQuadro, type NovoAjuste, quadroHC } from '@/services/modulos/quadro_hc';

export const chavesQuadroHC = {
  tudo: ['m', 'quadro_hc'] as const,
  quadro: (f: FiltroQuadro) => ['m', 'quadro_hc', 'quadro', f] as const,
  opcoes: ['m', 'quadro_hc', 'opcoes'] as const,
  loja: (id: number, f: FiltroQuadro) => ['m', 'quadro_hc', 'loja', id, f] as const,
  pessoa: (id: number) => ['m', 'quadro_hc', 'pessoa', id] as const,
};

export function useQuadroHC(filtro: FiltroQuadro) {
  // Troca de filtro mantém a árvore anterior na tela até a nova chegar (sem piscar o esqueleto).
  return useQuery({ queryKey: chavesQuadroHC.quadro(filtro), queryFn: () => quadroHC.quadro(filtro), placeholderData: keepPreviousData });
}

export function useOpcoesQuadroHC() {
  return useQuery({ queryKey: chavesQuadroHC.opcoes, queryFn: quadroHC.opcoes, staleTime: 10 * 60_000 });
}

export function useLojaHC(id: number, filtro: FiltroQuadro) {
  return useQuery({ queryKey: chavesQuadroHC.loja(id, filtro), queryFn: () => quadroHC.loja(id, filtro), enabled: id > 0, placeholderData: keepPreviousData });
}

export function usePessoaHC(id: number) {
  return useQuery({ queryKey: chavesQuadroHC.pessoa(id), queryFn: () => quadroHC.pessoa(id), enabled: id > 0 });
}

/** Ajuste manual muda a conta de mais de uma loja: recarrega o quadro todo. */
export function useSalvarAjuste() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (ajuste: NovoAjuste) => quadroHC.salvarAjuste(ajuste),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: chavesQuadroHC.tudo }),
  });
}

export function useSalvarPrevisto(loja: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (quantidades: Record<string, number>) => quadroHC.salvarPrevisto(loja, quantidades),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: chavesQuadroHC.tudo }),
  });
}
