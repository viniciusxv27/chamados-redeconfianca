/** Hooks do módulo Facilitador (TanStack Query sobre src/services/modulos/facilitador.ts). */
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';

import { facilitador, type TipoRecurso } from '@/services/modulos/facilitador';

export function useFacilitador(f: { busca?: string; categoria?: string; tipo?: TipoRecurso }) {
  return useQuery({ queryKey: ['m', 'facilitador', 'lista', f], queryFn: () => facilitador.listar(f), placeholderData: keepPreviousData });
}

export function useRecurso(id: number) {
  return useQuery({ queryKey: ['m', 'facilitador', 'recurso', id], queryFn: () => facilitador.detalhe(id), enabled: id > 0 });
}

export function useAbrirRecurso() {
  return useMutation({ mutationFn: ({ id, como }: { id: number; como?: 'arquivo' }) => facilitador.abrir(id, como) });
}
