/** Hooks do módulo Usuários (TanStack Query sobre src/services/modulos/usuarios.ts). */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';

import { type FiltrosUsuarios, usuarios } from '@/services/modulos/usuarios';

export const chavesUsuarios = {
  lista: (f: FiltrosUsuarios) => ['m', 'usuarios', 'lista', f] as const,
  opcoes: ['m', 'usuarios', 'opcoes'] as const,
  detalhe: (id: number) => ['m', 'usuarios', 'detalhe', id] as const,
};

export function useUsuarios(filtro: FiltrosUsuarios) {
  return useInfiniteQuery({
    queryKey: chavesUsuarios.lista(filtro),
    queryFn: ({ pageParam }) => usuarios.listar(filtro, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

export function useOpcoesUsuarios() {
  return useQuery({ queryKey: chavesUsuarios.opcoes, queryFn: usuarios.opcoes, staleTime: 10 * 60_000 });
}

export function useUsuario(id: number) {
  return useQuery({ queryKey: chavesUsuarios.detalhe(id), queryFn: () => usuarios.detalhe(id), enabled: id > 0 });
}
