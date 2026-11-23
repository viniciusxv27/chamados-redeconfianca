/** Hooks da Agenda (TanStack Query sobre src/services/modulos/agenda.ts). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { agenda, type CamposEvento, type CamposSolicitacao, type DetalheEvento, type DetalheTranscricao, type ImportarImpulso, type Resposta } from '@/services/modulos/agenda';

export const chavesAgenda = {
  tudo: ['m', 'agenda'] as const,
  semanas: ['m', 'agenda', 'semana'] as const,
  semana: (inicio: string, fim: string, usuario?: number) => ['m', 'agenda', 'semana', inicio, fim, usuario ?? 0] as const,
  disponibilidade: (usuario: number, dia: string) => ['m', 'agenda', 'disponibilidade', usuario, dia] as const,
  solicitacoes: (aba: string) => ['m', 'agenda', 'solicitacoes', aba] as const,
  evento: (id: number) => ['m', 'agenda', 'evento', id] as const,
  opcoes: ['m', 'agenda', 'opcoes'] as const,
  convites: ['m', 'agenda', 'convites'] as const,
  conflitos: (inicio: string, fim: string, ignorar?: number) => ['m', 'agenda', 'conflitos', inicio, fim, ignorar ?? 0] as const,
  transcricoes: ['m', 'agenda', 'transcricoes'] as const,
  transcricao: (id: number) => ['m', 'agenda', 'transcricao', id] as const,
};

export function useSemana(inicio: string, fim: string, usuario?: number) {
  return useQuery({ queryKey: chavesAgenda.semana(inicio, fim, usuario), queryFn: () => agenda.semana(inicio, fim, usuario), placeholderData: (anterior) => anterior });
}

/** Horários livres da pessoa num dia (blocos de 30 min das 8h às 18h, como no portal). */
export function useDisponibilidade(usuario: number, dia: string) {
  return useQuery({ queryKey: chavesAgenda.disponibilidade(usuario, dia), queryFn: () => agenda.disponibilidade(usuario, dia), enabled: usuario > 0, placeholderData: (anterior) => anterior });
}

export function useSolicitacoes(aba: 'recebidas' | 'enviadas') {
  return useQuery({ queryKey: chavesAgenda.solicitacoes(aba), queryFn: () => agenda.solicitacoes(aba) });
}

export function useSolicitarReuniao(usuario: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (campos: CamposSolicitacao) => agenda.solicitar(usuario, campos),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: ['m', 'agenda', 'solicitacoes'] }),
  });
}

export function useCancelarSolicitacao() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => agenda.cancelarSolicitacao(id),
    onSuccess: () => void cliente.invalidateQueries({ queryKey: ['m', 'agenda', 'solicitacoes'] }),
  });
}

export function useEvento(id: number) {
  return useQuery({ queryKey: chavesAgenda.evento(id), queryFn: () => agenda.evento(id), enabled: id > 0 });
}

/** Tipos, cores e pessoas mudam pouco: ficam prontos por 10 min. */
export function useOpcoesAgenda() {
  return useQuery({ queryKey: chavesAgenda.opcoes, queryFn: agenda.opcoes, staleTime: 10 * 60_000 });
}

export function useConvites() {
  return useQuery({ queryKey: chavesAgenda.convites, queryFn: agenda.convites });
}

/** Aviso de choque de horário (não impede salvar — o portal também não impede). */
export function useConflitos(inicio: string | null, fim: string | null, ignorar?: number) {
  return useQuery({
    queryKey: chavesAgenda.conflitos(inicio ?? '', fim ?? '', ignorar),
    queryFn: () => agenda.conflitos(inicio as string, fim as string, ignorar),
    enabled: !!inicio && !!fim,
    staleTime: 30_000,
  });
}

/** Depois de mexer num evento ou convite, a semana e os convites se refazem. */
function atualizarAgenda(cliente: ReturnType<typeof useQueryClient>) {
  void cliente.invalidateQueries({ queryKey: chavesAgenda.semanas });
  void cliente.invalidateQueries({ queryKey: chavesAgenda.convites });
  void cliente.invalidateQueries({ queryKey: ['m', 'agenda', 'conflitos'] });
}

export function useSalvarEvento(id?: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (campos: CamposEvento) => (id ? agenda.editar(id, campos) : agenda.criar(campos)),
    onSuccess: (r) => {
      cliente.setQueryData<DetalheEvento>(chavesAgenda.evento(r.evento.id), r.evento);
      atualizarAgenda(cliente);
    },
  });
}

export function useExcluirEvento(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (serie: boolean) => agenda.excluir(id, serie),
    onSuccess: () => {
      cliente.removeQueries({ queryKey: chavesAgenda.evento(id) });
      atualizarAgenda(cliente);
    },
  });
}

export function useResponderConvite() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ id, resposta, observacao }: { id: number; resposta: Resposta; observacao?: string }) => agenda.responderConvite(id, resposta, observacao),
    onSuccess: (r) => {
      void cliente.invalidateQueries({ queryKey: chavesAgenda.evento(r.evento_id) });
      atualizarAgenda(cliente);
    },
  });
}

export function useResponderSolicitacao() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ id, resposta, observacao }: { id: number; resposta: Resposta; observacao?: string }) => agenda.responderSolicitacao(id, resposta, observacao),
    onSuccess: () => {
      atualizarAgenda(cliente);
      void cliente.invalidateQueries({ queryKey: ['m', 'agenda', 'solicitacoes'] });
    },
  });
}

export function useTranscricoes() {
  return useInfiniteQuery({
    queryKey: chavesAgenda.transcricoes,
    queryFn: ({ pageParam }) => agenda.transcricoes(pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

/** Enquanto a transcrição processa, a tela pergunta de novo a cada 15 s (o portal faz polling também). */
export function useTranscricao(id: number) {
  return useQuery({
    queryKey: chavesAgenda.transcricao(id),
    queryFn: () => agenda.transcricao(id),
    enabled: id > 0,
    refetchInterval: (q) => (q.state.data && ['processing', 'recording'].includes(q.state.data.status) ? 15_000 : false),
  });
}

function trocarTranscricao(cliente: ReturnType<typeof useQueryClient>, id: number, t: DetalheTranscricao) {
  cliente.setQueryData<DetalheTranscricao>(chavesAgenda.transcricao(id), t);
  void cliente.invalidateQueries({ queryKey: chavesAgenda.transcricoes });
}

export function useAtribuirTarefa(transcricao: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ tarefa, usuario }: { tarefa: number; usuario: number }) => agenda.atribuir(transcricao, tarefa, usuario),
    onSuccess: (r) => trocarTranscricao(cliente, transcricao, r.transcricao),
  });
}

export function useImportarImpulso(transcricao: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: ({ tarefa, campos }: { tarefa: number; campos: ImportarImpulso }) => agenda.importarImpulso(transcricao, tarefa, campos),
    onSuccess: (r) => {
      trocarTranscricao(cliente, transcricao, r.transcricao);
      void cliente.invalidateQueries({ queryKey: ['m', 'impulso'] });
    },
  });
}

export function useCompartilharTranscricao(transcricao: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (usuarios: number[]) => agenda.compartilhar(transcricao, usuarios),
    onSuccess: (r) => trocarTranscricao(cliente, transcricao, r.transcricao),
  });
}
