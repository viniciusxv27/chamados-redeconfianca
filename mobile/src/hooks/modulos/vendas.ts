/** Hooks do módulo Vendas (TanStack Query sobre src/services/modulos/vendas.ts). */
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type FiltroVendas, lancar, type NovoOrcamento, type PodeVendas, type ResumoVendas, type SituacaoCliente, vendas } from '@/services/modulos/vendas';

const proxima = (ultima: { tem_mais: boolean; pagina: number }) => (ultima.tem_mais ? ultima.pagina + 1 : undefined);

export function useResumoVendas(f: FiltroVendas) {
  return useQuery({ queryKey: ['m', 'vendas', 'resumo', f], queryFn: () => vendas.resumo(f), placeholderData: keepPreviousData });
}

/** O que a pessoa pode em Vendas, do resumo já carregado na entrada do módulo (o servidor confere de novo). */
export function usePodeVendas(): PodeVendas | null {
  const cliente = useQueryClient();
  const achado = cliente.getQueriesData<ResumoVendas>({ queryKey: ['m', 'vendas', 'resumo'] }).find(([, d]) => d);
  return achado?.[1]?.pode ?? null;
}

export function useVendas(f: FiltroVendas) {
  return useInfiniteQuery({
    queryKey: ['m', 'vendas', 'lista', f],
    queryFn: ({ pageParam }) => vendas.listar(f, pageParam),
    initialPageParam: 1,
    getNextPageParam: proxima,
    placeholderData: keepPreviousData,
  });
}

export function useVenda(id: number) {
  return useQuery({ queryKey: ['m', 'vendas', 'venda', id], queryFn: () => vendas.detalhe(id), enabled: id > 0 });
}

export function usePrecos(f: { busca?: string; categoria?: string }) {
  return useInfiniteQuery({
    queryKey: ['m', 'vendas', 'precos', f],
    queryFn: ({ pageParam }) => vendas.precos(f, pageParam),
    initialPageParam: 1,
    getNextPageParam: proxima,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useProduto(id: number) {
  return useQuery({ queryKey: ['m', 'vendas', 'produto', id], queryFn: () => vendas.produto(id), enabled: id > 0, staleTime: 5 * 60_000 });
}

export function useClientes(f: { busca?: string; situacao?: SituacaoCliente }) {
  return useInfiniteQuery({
    queryKey: ['m', 'vendas', 'clientes', f],
    queryFn: ({ pageParam }) => vendas.clientes(f, pageParam),
    initialPageParam: 1,
    getNextPageParam: proxima,
    placeholderData: keepPreviousData,
  });
}

export function useCliente(cpf: string) {
  return useQuery({ queryKey: ['m', 'vendas', 'cliente', cpf], queryFn: () => vendas.cliente(cpf), enabled: !!cpf });
}

export function useOrcamentos(busca?: string) {
  return useInfiniteQuery({
    queryKey: ['m', 'vendas', 'orcamentos', busca ?? ''],
    queryFn: ({ pageParam }) => vendas.orcamentos(busca, pageParam),
    initialPageParam: 1,
    getNextPageParam: proxima,
  });
}

export function useCriarOrcamento() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (o: NovoOrcamento) => vendas.criarOrcamento(o),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: ['m', 'vendas', 'orcamentos'] }),
  });
}

// ── Lançar venda ──

export function useFormularioVenda() {
  return useQuery({ queryKey: ['m', 'vendas', 'lancar', 'formulario'], queryFn: lancar.formulario, staleTime: 5 * 60_000 });
}

export function useBuscarClienteVenda() {
  return useMutation({ mutationFn: (cpf: string) => lancar.cliente(cpf) });
}

export function useProdutosVenda(q: string, segmentacao: string) {
  return useQuery({
    queryKey: ['m', 'vendas', 'lancar', 'produtos', q, segmentacao],
    queryFn: () => lancar.produtos(q, segmentacao),
    enabled: q.length >= 2,
    placeholderData: keepPreviousData,
  });
}

export function useConferirRenova() {
  return useMutation({ mutationFn: (codigo: string) => lancar.renova(codigo) });
}

export function usePlanosFora(q: string) {
  return useQuery({ queryKey: ['m', 'vendas', 'lancar', 'fora', q], queryFn: () => lancar.planosFora(q), enabled: q.length >= 2, placeholderData: keepPreviousData });
}

export function useGravarVenda() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (payload: object) => lancar.gravar(payload),
    onSuccess: (r) => {
      if (r.ok) void cliente.invalidateQueries({ queryKey: ['m', 'vendas'], predicate: (q) => q.queryKey[2] !== 'lancar' });
    },
  });
}
