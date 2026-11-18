/** Hooks do módulo Cursos (TanStack Query sobre src/services/modulos/cursos.ts). */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type ArquivoComprovante, cursos } from '@/services/modulos/cursos';

export const chavesCursos = {
  tudo: ['m', 'cursos'] as const,
  meus: ['m', 'cursos', 'meus'] as const,
  detalhe: (id: number) => ['m', 'cursos', 'detalhe', id] as const,
  gestao: (curso: number) => ['m', 'cursos', 'gestao', curso] as const,
};

export function useMeusCursos() {
  return useQuery({ queryKey: chavesCursos.meus, queryFn: cursos.meus });
}

/** O detalhe traz o link do comprovante (vale 5 min): refaz a busca depois de 4 min parado. */
export function useCurso(id: number) {
  return useQuery({ queryKey: chavesCursos.detalhe(id), queryFn: () => cursos.detalhe(id), enabled: id > 0, staleTime: 4 * 60_000 });
}

export function useQuadroCursos(curso: number) {
  return useQuery({ queryKey: chavesCursos.gestao(curso), queryFn: () => cursos.gestao(curso || undefined), placeholderData: keepPreviousData, staleTime: 4 * 60_000 });
}

export function useEnviarComprovante(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (arquivo: ArquivoComprovante) => cursos.enviarComprovante(id, arquivo),
    onSuccess: (detalhe) => {
      cliente.setQueryData(chavesCursos.detalhe(id), detalhe);
      void cliente.invalidateQueries({ queryKey: chavesCursos.meus });
      void cliente.invalidateQueries({ queryKey: ['m', 'cursos', 'gestao'] });
    },
  });
}
