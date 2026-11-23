/** Hooks da Gestão de metas (TanStack Query sobre src/services/modulos/gestao_metas.ts). */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type CampoMeta, type Competencia, gestaoMetas } from '@/services/modulos/gestao_metas';

export const chavesGestaoMetas = {
  tudo: ['m', 'gestao_metas'] as const,
  inicio: ['m', 'gestao_metas', 'inicio'] as const,
  competencia: (codigo: string, pilar: string) => ['m', 'gestao_metas', 'competencia', codigo, pilar] as const,
  agora: (codigo: string) => ['m', 'gestao_metas', 'agora', codigo] as const,
  historico: (codigo: string, pilar: string) => ['m', 'gestao_metas', 'historico', codigo, pilar] as const,
};

export function useInicioGestaoMetas() {
  return useQuery({ queryKey: chavesGestaoMetas.inicio, queryFn: gestaoMetas.inicio });
}

export function useCompetencia(codigo: string, pilar: string) {
  return useQuery({
    queryKey: chavesGestaoMetas.competencia(codigo, pilar),
    queryFn: () => gestaoMetas.competencia(codigo, pilar),
    enabled: !!codigo,
    placeholderData: keepPreviousData,
  });
}

/** "Realizado até o momento": o portal guarda 10 min; aqui 5 min sem buscar de novo. */
export function useRealizadoAgora(codigo: string, ligado: boolean) {
  return useQuery({ queryKey: chavesGestaoMetas.agora(codigo), queryFn: () => gestaoMetas.realizadoAgora(codigo), enabled: !!codigo && ligado, staleTime: 5 * 60_000, retry: 0 });
}

export function useHistoricoMetas(codigo: string, pilar: string) {
  return useQuery({ queryKey: chavesGestaoMetas.historico(codigo, pilar), queryFn: () => gestaoMetas.historico(codigo, pilar), enabled: !!codigo });
}

/** Troca a linha da loja na tela sem recarregar o pilar inteiro (os subtotais se refazem depois). */
function trocarLinha(cliente: ReturnType<typeof useQueryClient>, codigo: string, pilar: string, linha: Competencia['linhas'][number] | null) {
  if (!linha) return;
  cliente.setQueryData<Competencia>(chavesGestaoMetas.competencia(codigo, pilar), (c) =>
    c ? { ...c, linhas: c.linhas.map((l) => (l.loja_id === linha.loja_id ? linha : l)) } : c,
  );
  void cliente.invalidateQueries({ queryKey: chavesGestaoMetas.competencia(codigo, pilar) });
  void cliente.invalidateQueries({ queryKey: ['m', 'gestao_metas', 'historico', codigo] });
}

export function useSalvarMeta(codigo: string, pilar: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: { loja: number; campo: CampoMeta; valor: string }) => gestaoMetas.salvar(codigo, { ...corpo, pilar }),
    onSuccess: (r) => trocarLinha(cliente, codigo, pilar, r.linha),
  });
}

export function useDividirMeta(codigo: string, pilar: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: { loja: number; campos: Partial<Record<CampoMeta, string>> }) => gestaoMetas.dividir(codigo, { ...corpo, pilar }),
    onSuccess: (r) => trocarLinha(cliente, codigo, pilar, r.linha),
  });
}

export function usePreencherMetas(codigo: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: { pilar: string; origem: string; percentual: string; arredondar: string; somente_vazias: boolean }) => gestaoMetas.preencher(codigo, corpo),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: chavesGestaoMetas.tudo }),
  });
}

export function useFecharCompetencia(codigo: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (acao: 'fechar' | 'reabrir') => gestaoMetas.fechar(codigo, acao),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: chavesGestaoMetas.tudo }),
  });
}
