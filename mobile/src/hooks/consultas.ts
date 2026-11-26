/** Hooks de dados das telas (TanStack Query sobre src/services/portal.ts). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  buscarImpulsoHome,
  buscarInicio,
  chamados,
  comunicados,
  type FiltroChamados,
  listarModulos,
  notificacoes,
  reconhecimentos,
  social,
} from '@/services/portal';
import type { Chamado, Comunicado, ComunicadoResumo, FiltroMural, Notificacao, Pagina, PaginaSocial, PostSocial } from '@/services/tipos';

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
  destinatarios: (id: number, situacao: string, busca: string) => ['comunicado', id, 'destinatarios', situacao, busca] as const,
  reconhecimentos: (ver: FiltroMural, busca: string) => ['reconhecimentos', ver, busca] as const,
  impulsoHome: ['impulso', 'home'] as const,
  social: ['social'] as const,
  socialLista: (limite: number) => ['social', 'lista', limite] as const,
  socialPost: (id: number) => ['social', 'post', id] as const,
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

/** Status de cada destinatário do comunicado, em páginas de 20 (filtro por situação e busca no portal). */
export function useDestinatarios(id: number, situacao: string, busca: string, ligado = true) {
  return useInfiniteQuery({
    queryKey: chaves.destinatarios(id, situacao, busca),
    queryFn: ({ pageParam }) => comunicados.destinatarios(id, { situacao, busca }, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
    enabled: id > 0 && ligado,
    placeholderData: (anterior) => anterior,
  });
}

/** Mural de Reconhecimento: 24 por página, com filtro e busca. */
export function useReconhecimentos(ver: FiltroMural, busca: string) {
  return useInfiniteQuery({
    queryKey: chaves.reconhecimentos(ver, busca),
    queryFn: ({ pageParam }) => reconhecimentos.listar({ ver, busca }, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
    placeholderData: (anterior) => anterior,
  });
}

/** Cartão do Impulso da home (null = não é do Impulso: o cartão some, como no portal). */
export function useImpulsoHome() {
  return useQuery({ queryKey: chaves.impulsoHome, queryFn: buscarImpulsoHome, staleTime: 2 * 60_000 });
}

/** Feed da rede: a home pede 3, a tela do feed 8 por vez; anda para trás pelo id do último. */
export function useFeedSocial(limite = 8) {
  return useInfiniteQuery({
    queryKey: chaves.socialLista(limite),
    queryFn: ({ pageParam }) => social.listar(pageParam, limite),
    initialPageParam: null as number | null,
    getNextPageParam: (ultima) => (ultima.tem_mais && ultima.ultimo ? ultima.ultimo : undefined),
  });
}

export function usePostSocial(id: number) {
  return useQuery({ queryKey: chaves.socialPost(id), queryFn: () => social.post(id), enabled: id > 0 });
}

type PaginasSocial = { pages: PaginaSocial[]; pageParams: (number | null)[] };

/** Muda um post em todo lugar onde ele aparece (home, feed e a tela do post). */
function aplicarNoPost(cliente: ReturnType<typeof useQueryClient>, id: number, mudar: (p: PostSocial) => PostSocial | null) {
  cliente.setQueryData<PostSocial>(chaves.socialPost(id), (p) => (p ? (mudar(p) ?? p) : p));
  cliente.setQueriesData<PaginasSocial>({ queryKey: [...chaves.social, 'lista'] }, (dados) =>
    dados?.pages
      ? {
          ...dados,
          pages: dados.pages.map((pg) => ({
            ...pg,
            resultados: pg.resultados.flatMap((p) => {
              if (p.id !== id) return [p];
              const novo = mudar(p);
              return novo ? [novo] : [];
            }),
          })),
        }
      : dados,
  );
}

/** Curtir com resposta instantânea (muda antes e confirma com o portal). */
export function useCurtirPost() {
  const cliente = useQueryClient();
  const inverter = (p: PostSocial) => ({ ...p, curti: !p.curti, curtidas: Math.max(0, p.curtidas + (p.curti ? -1 : 1)) });
  return useMutation({
    mutationFn: (id: number) => social.curtir(id),
    onMutate: (id) => aplicarNoPost(cliente, id, inverter),
    onSuccess: (r, id) => aplicarNoPost(cliente, id, (p) => ({ ...p, curti: r.curti, curtidas: r.curtidas })),
    onError: (_e, id) => aplicarNoPost(cliente, id, inverter),
  });
}

export function useComentarPost(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (texto: string) => social.comentar(id, texto),
    onSuccess: ({ comentario, total_comentarios }) => {
      cliente.setQueryData<PostSocial>(chaves.socialPost(id), (p) =>
        p ? { ...p, comentarios: [...p.comentarios, comentario], total_comentarios } : p,
      );
      // No feed ficam os 3 últimos, como no portal.
      aplicarNoPost(cliente, id, (p) => ({ ...p, total_comentarios, comentarios: [...p.comentarios, comentario].slice(-3) }));
    },
  });
}

export function useApagarComentario(postId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (comentarioId: number) => social.apagarComentario(comentarioId),
    onSuccess: ({ total_comentarios }, comentarioId) => {
      aplicarNoPost(cliente, postId, (p) => ({ ...p, total_comentarios, comentarios: p.comentarios.filter((c) => c.id !== comentarioId) }));
      void cliente.invalidateQueries({ queryKey: chaves.social });
    },
  });
}

export function useApagarPost() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => social.apagar(id),
    onSuccess: (_r, id) => {
      aplicarNoPost(cliente, id, () => null);
      cliente.removeQueries({ queryKey: chaves.socialPost(id) });
    },
  });
}

export function usePublicarPost() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: social.publicar,
    onSuccess: () => void cliente.invalidateQueries({ queryKey: chaves.social }),
  });
}
