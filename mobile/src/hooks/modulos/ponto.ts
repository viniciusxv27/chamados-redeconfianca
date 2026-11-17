/** Hooks do módulo Ponto (TanStack Query sobre src/services/modulos/ponto.ts). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type AcaoSolicitacao, type CamposSolicitacao, type FiltroCaixa, type FiltroSolicitacoes, ponto, type Solicitacao } from '@/services/modulos/ponto';

export const chavesPonto = {
  tudo: ['m', 'ponto'] as const,
  resumo: ['m', 'ponto', 'resumo'] as const,
  marcacoes: ['m', 'ponto', 'marcacoes'] as const,
  ferias: ['m', 'ponto', 'ferias'] as const,
  escala: (inicio: string) => ['m', 'ponto', 'escala', inicio] as const,
  formulario: ['m', 'ponto', 'formulario'] as const,
  registrado: (dia: string) => ['m', 'ponto', 'registrado', dia] as const,
  solicitacoes: (filtro: FiltroSolicitacoes) => ['m', 'ponto', 'solicitacoes', filtro] as const,
  solicitacao: (id: number) => ['m', 'ponto', 'solicitacao', id] as const,
  caixa: (filtro: FiltroCaixa, busca: string) => ['m', 'ponto', 'caixa', filtro, busca] as const,
};

export function useResumoPonto() {
  return useQuery({ queryKey: chavesPonto.resumo, queryFn: ponto.resumo });
}

/** Marcações vêm do Tangerino (o portal guarda 2 min em cache): não precisa refazer a cada foco. */
export function useMarcacoes(ativo = true) {
  return useQuery({ queryKey: chavesPonto.marcacoes, queryFn: ponto.marcacoes, enabled: ativo, staleTime: 60_000 });
}

export function useFerias() {
  return useQuery({ queryKey: chavesPonto.ferias, queryFn: ponto.ferias, staleTime: 5 * 60_000 });
}

export function useEscala(inicio: string) {
  return useQuery({ queryKey: chavesPonto.escala(inicio), queryFn: () => ponto.escala(inicio || undefined) });
}

export function useFormularioPonto() {
  return useQuery({ queryKey: chavesPonto.formulario, queryFn: ponto.formulario, staleTime: 30 * 60_000 });
}

/** O que o relógio registrou no dia escolhido (para conferir antes de pedir o ajuste). */
export function useRegistradoNoDia(dia: string | null) {
  return useQuery({
    queryKey: chavesPonto.registrado(dia ?? ''),
    queryFn: () => ponto.registradoNoDia(dia ?? ''),
    enabled: !!dia,
    staleTime: 5 * 60_000,
  });
}

export function useSolicitacoes(filtro: FiltroSolicitacoes) {
  return useInfiniteQuery({
    queryKey: chavesPonto.solicitacoes(filtro),
    queryFn: ({ pageParam }) => ponto.solicitacoes(filtro, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

export function useSolicitacao(id: number) {
  return useQuery({ queryKey: chavesPonto.solicitacao(id), queryFn: () => ponto.solicitacao(id), enabled: id > 0 });
}

export function useCaixaDP(filtro: FiltroCaixa, busca: string) {
  return useInfiniteQuery({
    queryKey: chavesPonto.caixa(filtro, busca),
    queryFn: ({ pageParam }) => ponto.caixa(filtro, busca, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

/** Depois de qualquer escrita: a solicitação nova/alterada vai para o detalhe e as listas recarregam. */
function useAoGravar() {
  const cliente = useQueryClient();
  return (sol: Solicitacao) => {
    cliente.setQueryData(chavesPonto.solicitacao(sol.id), sol);
    void cliente.invalidateQueries({ queryKey: ['m', 'ponto', 'solicitacoes'] });
    void cliente.invalidateQueries({ queryKey: ['m', 'ponto', 'caixa'] });
    void cliente.invalidateQueries({ queryKey: chavesPonto.resumo });
  };
}

export function useEnviarSolicitacao() {
  const aoGravar = useAoGravar();
  return useMutation({ mutationFn: (campos: CamposSolicitacao) => ponto.enviar(campos), onSuccess: aoGravar });
}

export function useCorrigirSolicitacao(id: number) {
  const aoGravar = useAoGravar();
  return useMutation({ mutationFn: (campos: CamposSolicitacao) => ponto.corrigir(id, campos), onSuccess: aoGravar });
}

export function useAcaoSolicitacao(id: number) {
  const aoGravar = useAoGravar();
  return useMutation({ mutationFn: (acao: AcaoSolicitacao) => ponto.acao(id, acao), onSuccess: aoGravar });
}

export function useCancelarAjusteSolides() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => ponto.cancelarAjusteSolides(id),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: chavesPonto.marcacoes }),
  });
}
