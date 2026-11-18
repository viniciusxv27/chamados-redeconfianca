/** Hooks do módulo Mercadinho (TanStack Query sobre src/services/modulos/mercadinho.ts). */
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type FiltroVitrine, mercadinho, type StatusResgate } from '@/services/modulos/mercadinho';

const proxima = (ultima: { tem_mais: boolean; pagina: number }) => (ultima.tem_mais ? ultima.pagina + 1 : undefined);

export const chavesMercadinho = {
  tudo: ['m', 'mercadinho'] as const,
  vitrine: (f: FiltroVitrine) => ['m', 'mercadinho', 'vitrine', f] as const,
  premio: (id: number) => ['m', 'mercadinho', 'premio', id] as const,
  resgates: (situacao?: StatusResgate) => ['m', 'mercadinho', 'resgates', situacao ?? 'todos'] as const,
};

export function useVitrine(f: FiltroVitrine) {
  return useInfiniteQuery({
    queryKey: chavesMercadinho.vitrine(f),
    queryFn: ({ pageParam }) => mercadinho.vitrine(f, pageParam),
    initialPageParam: 1,
    getNextPageParam: proxima,
    placeholderData: keepPreviousData,
  });
}

export function usePremio(id: number) {
  return useQuery({ queryKey: chavesMercadinho.premio(id), queryFn: () => mercadinho.premio(id), enabled: id > 0 });
}

export function useResgates(situacao?: StatusResgate) {
  return useInfiniteQuery({
    queryKey: chavesMercadinho.resgates(situacao),
    queryFn: ({ pageParam }) => mercadinho.resgates(situacao, pageParam),
    initialPageParam: 1,
    getNextPageParam: proxima,
    placeholderData: keepPreviousData,
  });
}

/** Saldo, estoque e resgates mudam juntos: depois de resgatar/cancelar, tudo do módulo recarrega. */
function useRecarregarTudo() {
  const cliente = useQueryClient();
  return () => cliente.invalidateQueries({ queryKey: chavesMercadinho.tudo });
}

export function useResgatar(id: number) {
  const recarregar = useRecarregarTudo();
  return useMutation({ mutationFn: () => mercadinho.resgatar(id), onSuccess: () => void recarregar() });
}

export function useCancelarResgate() {
  const recarregar = useRecarregarTudo();
  return useMutation({ mutationFn: (id: number) => mercadinho.cancelar(id), onSuccess: () => void recarregar() });
}

export function useSugerirProduto() {
  return useMutation({ mutationFn: mercadinho.sugerir });
}
