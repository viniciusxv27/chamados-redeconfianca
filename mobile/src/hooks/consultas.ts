/** Hooks de dados das telas (TanStack Query sobre src/services/portal.ts). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  buscarInicio,
  chamados,
  comunicados,
  type FiltroChamados,
  listarModulos,
  notificacoes,
} from '@/services/portal';
import type { Chamado, Comunicado, ComunicadoResumo, Notificacao, Pagina } from '@/services/tipos';

export const chaves = {
  inicio: ['inicio'] as const,
  chamados: (f: FiltroChamados) => ['chamados', f] as const,
  chamado: (id: number) => ['chamado', id] as const,
  setores: ['setores'] as const,
  categorias: (setor: number) => ['categorias', setor] as const,
  comunicados: ['comunicados'] as const,
  feed: ['comunicados', 'feed'] as const,
  comunicado: (id: number) => ['comunicado', id] as const,
  notificacoes: ['notificacoes'] as const,
  modulos: ['modulos'] as const,
};

export function useInicio() {
  return useQuery({ queryKey: chaves.inicio, queryFn: buscarInicio });
}

export function useChamados(filtro: FiltroChamados) {
  return useInfiniteQuery({
    queryKey: chaves.chamados(filtro),
    queryFn: ({ pageParam }) => chamados.listar(filtro, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

export function useChamado(id: number) {
  return useQuery({ queryKey: chaves.chamado(id), queryFn: () => chamados.detalhe(id), enabled: id > 0 });
}

export function useComentar(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (texto: string) => chamados.comentar(id, texto),
    onSuccess: (comentario) => {
      cliente.setQueryData<Chamado>(chaves.chamado(id), (atual) =>
        atual ? { ...atual, comentarios: [...atual.comentarios, comentario] } : atual,
      );
      void cliente.invalidateQueries({ queryKey: ['chamados'] });
    },
  });
}

export function useSetores() {
  return useQuery({ queryKey: chaves.setores, queryFn: chamados.setores, staleTime: 10 * 60_000 });
}

export function useCategorias(setor: number | null) {
  return useQuery({
    queryKey: chaves.categorias(setor ?? 0),
    queryFn: () => chamados.categorias(setor ?? 0),
    enabled: !!setor,
    staleTime: 10 * 60_000,
  });
}

export function useAbrirChamado() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: chamados.abrir,
    onSuccess: () => {
      void cliente.invalidateQueries({ queryKey: ['chamados'] });
      void cliente.invalidateQueries({ queryKey: chaves.inicio });
    },
  });
}

export function useComunicados() {
  return useInfiniteQuery({
    queryKey: chaves.comunicados,
    queryFn: ({ pageParam }) => comunicados.listar(pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

/** "Feed de Comunicados" da home: só os não fixados, 10 por vez ("Carregar mais", como no portal). */
export function useFeed() {
  return useInfiniteQuery({
    queryKey: chaves.feed,
    queryFn: ({ pageParam }) => comunicados.listar(pageParam, true),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

export function useComunicado(id: number) {
  return useQuery({ queryKey: chaves.comunicado(id), queryFn: () => comunicados.detalhe(id), enabled: id > 0 });
}

type PaginasComunicados = { pages: Pagina<ComunicadoResumo>[]; pageParams: number[] };

/** Atualiza um comunicado em todo lugar onde ele aparece (lista, detalhe, início). */
function aplicarNoComunicado(
  cliente: ReturnType<typeof useQueryClient>,
  id: number,
  mudar: (c: ComunicadoResumo) => ComunicadoResumo,
) {
  cliente.setQueryData<Comunicado>(chaves.comunicado(id), (c) => (c ? (mudar(c) as Comunicado) : c));
  // Mural e feed da home (todas as listas que começam com 'comunicados').
  cliente.setQueriesData<PaginasComunicados>({ queryKey: chaves.comunicados }, (dados) =>
    dados?.pages
      ? { ...dados, pages: dados.pages.map((p) => ({ ...p, resultados: p.resultados.map((c) => (c.id === id ? mudar(c) : c)) })) }
      : dados,
  );
}

/** Curtir com resposta instantânea: muda na tela antes e confirma com o portal. */
export function useCurtir() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => comunicados.curtir(id),
    onMutate: (id) => {
      aplicarNoComunicado(cliente, id, (c) => ({ ...c, curti: !c.curti, curtidas: c.curtidas + (c.curti ? -1 : 1) }));
    },
    onSuccess: (r, id) => aplicarNoComunicado(cliente, id, (c) => ({ ...c, curti: r.curti, curtidas: r.curtidas })),
    onError: (_e, id) => aplicarNoComunicado(cliente, id, (c) => ({ ...c, curti: !c.curti, curtidas: c.curtidas + (c.curti ? -1 : 1) })),
    onSettled: () => void cliente.invalidateQueries({ queryKey: chaves.inicio }),
  });
}

export function useSituacaoComunicado(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (situacao: 'ESTOU_CIENTE' | 'ESTOU_COM_DUVIDA') => comunicados.situacao(id, situacao),
    onSuccess: (r) => {
      aplicarNoComunicado(cliente, id, (c) => ({ ...c, situacao: r.situacao }));
      void cliente.invalidateQueries({ queryKey: chaves.inicio });
    },
  });
}

export function useNotificacoes() {
  return useQuery({ queryKey: chaves.notificacoes, queryFn: notificacoes.listar });
}

export function useMarcarLida() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => notificacoes.marcarLida(id),
    onMutate: (id) => {
      cliente.setQueryData<{ resultados: Notificacao[]; nao_lidas: number }>(chaves.notificacoes, (d) =>
        d
          ? {
              nao_lidas: Math.max(0, d.nao_lidas - (d.resultados.find((n) => n.id === id && !n.lida) ? 1 : 0)),
              resultados: d.resultados.map((n) => (n.id === id ? { ...n, lida: true } : n)),
            }
          : d,
      );
    },
    onSettled: () => void cliente.invalidateQueries({ queryKey: chaves.inicio }),
  });
}

export function useMarcarTodas() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: notificacoes.marcarTodas,
    onMutate: () => {
      cliente.setQueryData<{ resultados: Notificacao[]; nao_lidas: number }>(chaves.notificacoes, (d) =>
        d ? { nao_lidas: 0, resultados: d.resultados.map((n) => ({ ...n, lida: true })) } : d,
      );
    },
    onSettled: () => {
      void cliente.invalidateQueries({ queryKey: chaves.notificacoes });
      void cliente.invalidateQueries({ queryKey: chaves.inicio });
    },
  });
}

/** Módulos liberados para a pessoa (muda pouco: 5 min de cache). */
export function useModulos() {
  return useQuery({ queryKey: chaves.modulos, queryFn: listarModulos, staleTime: 5 * 60_000 });
}
