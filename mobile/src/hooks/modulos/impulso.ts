/** Hooks do Impulso (TanStack Query sobre src/services/modulos/impulso.ts). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type DetalheMeta, type FiltroKanban, impulso, type StatusMeta } from '@/services/modulos/impulso';

export const chavesImpulso = {
  tudo: ['m', 'impulso'] as const,
  resumo: ['m', 'impulso', 'resumo'] as const,
  ranking: ['m', 'impulso', 'ranking'] as const,
  kanban: (f: FiltroKanban) => ['m', 'impulso', 'kanban', f] as const,
  concluidas: (f: FiltroKanban, inicio: number) => ['m', 'impulso', 'concluidas', f, inicio] as const,
  meta: (id: number) => ['m', 'impulso', 'meta', id] as const,
};

export function useResumoImpulso() {
  return useQuery({ queryKey: chavesImpulso.resumo, queryFn: impulso.resumo });
}

export function useRanking() {
  return useQuery({ queryKey: chavesImpulso.ranking, queryFn: impulso.ranking, staleTime: 3 * 60_000 });
}

export function useKanban(filtro: FiltroKanban) {
  return useQuery({ queryKey: chavesImpulso.kanban(filtro), queryFn: () => impulso.kanban(filtro), placeholderData: (anterior) => anterior });
}

/** "Mostrar mais" da coluna Concluída: começa depois dos cards que o Kanban já trouxe. */
export function useMaisConcluidas(filtro: FiltroKanban, inicio: number, ativo: boolean) {
  return useInfiniteQuery({
    queryKey: chavesImpulso.concluidas(filtro, inicio),
    queryFn: ({ pageParam }) => impulso.maisConcluidas(filtro, pageParam),
    initialPageParam: inicio,
    getNextPageParam: (ultima) => (ultima.restantes > 0 ? ultima.proximo : undefined),
    enabled: ativo,
  });
}

export function useMeta(id: number) {
  return useQuery({ queryKey: chavesImpulso.meta(id), queryFn: () => impulso.meta(id), enabled: id > 0 });
}

/** Depois de mexer numa meta, o Kanban e o resumo precisam se refazer. */
function atualizarListas(cliente: ReturnType<typeof useQueryClient>) {
  void cliente.invalidateQueries({ queryKey: ['m', 'impulso', 'kanban'] });
  void cliente.invalidateQueries({ queryKey: ['m', 'impulso', 'concluidas'] });
  void cliente.invalidateQueries({ queryKey: chavesImpulso.resumo });
}

export function useMoverMeta(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (status: StatusMeta) => impulso.mover(id, status),
    onSuccess: (r) => {
      cliente.setQueryData<DetalheMeta>(chavesImpulso.meta(id), r.meta);
      atualizarListas(cliente);
    },
  });
}

/** Marcar o passo do to-do: muda na tela na hora e confirma com o portal. */
export function useMarcarItem(metaId: number) {
  const cliente = useQueryClient();
  const trocar = (itemId: number) =>
    cliente.setQueryData<DetalheMeta>(chavesImpulso.meta(metaId), (m) => {
      if (!m) return m;
      const itens = m.itens.map((i) => (i.id === itemId ? { ...i, concluido: !i.concluido } : i));
      return { ...m, itens, feitos: itens.filter((i) => i.concluido).length };
    });
  return useMutation({
    mutationFn: (itemId: number) => impulso.marcarItem(itemId),
    onMutate: (itemId) => trocar(itemId),
    onError: (_e, itemId) => trocar(itemId),
    onSuccess: () => {
      void cliente.invalidateQueries({ queryKey: chavesImpulso.meta(metaId) });
      void cliente.invalidateQueries({ queryKey: ['m', 'impulso', 'kanban'] });
    },
  });
}

export function useComentarMeta(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (texto: string) => impulso.comentar(id, texto),
    onSuccess: (c) => {
      cliente.setQueryData<DetalheMeta>(chavesImpulso.meta(id), (m) => (m ? { ...m, comentarios: [...m.comentarios, c] } : m));
    },
  });
}

export function useAvaliarMeta(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (notas: { nota_qualidade: number; nota_prazo: number; comentario?: string }) => impulso.avaliar(id, notas),
    onSuccess: (r) => {
      cliente.setQueryData<DetalheMeta>(chavesImpulso.meta(id), r.meta);
      atualizarListas(cliente);
    },
  });
}

export function useResponderConvite() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ id, resposta }: { id: number; resposta: 'aceitar' | 'recusar' }) => impulso.responderConvite(id, resposta),
    onSuccess: (r) => {
      void cliente.invalidateQueries({ queryKey: chavesImpulso.meta(r.meta_id) });
      atualizarListas(cliente);
    },
  });
}
