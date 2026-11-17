/** Hooks do módulo Comissão (TanStack Query sobre src/services/modulos/comissao.ts). */
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { comissao, type FiltroComissao } from '@/services/modulos/comissao';

export const chavesComissao = {
  resumo: (f: FiltroComissao) => ['m', 'comissao', 'resumo', f] as const,
  pessoas: (f: FiltroComissao) => ['m', 'comissao', 'pessoas', f] as const,
  historico: (escopo: string) => ['m', 'comissao', 'historico', escopo] as const,
  projecao: ['m', 'comissao', 'projecao'] as const,
};

/** A planilha muda poucas vezes por mês: 5 min sem buscar de novo. */
export function useComissao(filtro: FiltroComissao) {
  return useQuery({ queryKey: chavesComissao.resumo(filtro), queryFn: () => comissao.resumo(filtro), staleTime: 5 * 60_000, placeholderData: keepPreviousData });
}

export function usePessoasComissao(filtro: FiltroComissao, ligado: boolean) {
  return useQuery({ queryKey: chavesComissao.pessoas(filtro), queryFn: () => comissao.pessoas(filtro), enabled: ligado, staleTime: 10 * 60_000 });
}

export function useHistoricoComissao(escopo: 'eu' | 'equipe') {
  return useQuery({ queryKey: chavesComissao.historico(escopo), queryFn: () => comissao.historico(escopo), staleTime: 10 * 60_000 });
}

export function useProjecaoComissao() {
  return useQuery({ queryKey: chavesComissao.projecao, queryFn: comissao.projecao, staleTime: 5 * 60_000 });
}
