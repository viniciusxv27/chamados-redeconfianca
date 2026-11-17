/** Hooks do módulo Checklists (TanStack Query sobre src/services/modulos/checklists.ts). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type ArquivoLocal, checklists, type Execucao, type FiltroExecucoes, type Resposta, type Tarefa } from '@/services/modulos/checklists';

export const chavesChecklists = {
  tudo: ['m', 'checklists'] as const,
  inicio: ['m', 'checklists', 'inicio'] as const,
  lista: (filtro: FiltroExecucoes) => ['m', 'checklists', 'lista', filtro] as const,
  execucao: (id: number) => ['m', 'checklists', 'execucao', id] as const,
  acompanhamento: (dia: string) => ['m', 'checklists', 'acompanhamento', dia] as const,
  pessoa: (id: number, dia: string) => ['m', 'checklists', 'pessoa', id, dia] as const,
};

export function useInicioChecklists() {
  return useQuery({ queryKey: chavesChecklists.inicio, queryFn: checklists.inicio });
}

export function useExecucoes(filtro: FiltroExecucoes) {
  return useInfiniteQuery({
    queryKey: chavesChecklists.lista(filtro),
    queryFn: ({ pageParam }) => checklists.execucoes(filtro, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
  });
}

export function useExecucao(id: number) {
  return useQuery({ queryKey: chavesChecklists.execucao(id), queryFn: () => checklists.execucao(id), enabled: id > 0 });
}

export function useAcompanhamento(dia: string) {
  return useQuery({ queryKey: chavesChecklists.acompanhamento(dia), queryFn: () => checklists.acompanhamento(dia) });
}

export function useAcompanhamentoPessoa(id: number, dia: string) {
  return useQuery({ queryKey: chavesChecklists.pessoa(id, dia), queryFn: () => checklists.acompanhamentoPessoa(id, dia), enabled: id > 0 });
}

/** Troca uma tarefa dentro do detalhe em cache (depois de subir/excluir evidência). */
function useTrocarTarefa(execucaoId: number) {
  const cliente = useQueryClient();
  return (tarefa: Tarefa) => {
    cliente.setQueryData<Execucao>(chavesChecklists.execucao(execucaoId), (atual) =>
      atual ? { ...atual, tarefas: atual.tarefas.map((t) => (t.id === tarefa.id ? tarefa : t)) } : atual,
    );
  };
}

export function useSubirEvidencias(execucaoId: number) {
  const trocar = useTrocarTarefa(execucaoId);
  return useMutation({
    mutationFn: ({ tarefaId, arquivos }: { tarefaId: number; arquivos: ArquivoLocal[] }) => checklists.subirEvidencias(tarefaId, arquivos),
    onSuccess: (r) => trocar(r.tarefa),
  });
}

export function useExcluirEvidencia(execucaoId: number) {
  const trocar = useTrocarTarefa(execucaoId);
  return useMutation({ mutationFn: (id: number) => checklists.excluirEvidencia(id), onSuccess: (r) => trocar(r.tarefa) });
}

export function useEnviarChecklist(execucaoId: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (respostas: Resposta[]) => checklists.enviar(execucaoId, respostas),
    onSuccess: (r) => {
      cliente.setQueryData(chavesChecklists.execucao(execucaoId), r.execucao);
      void cliente.invalidateQueries({ queryKey: chavesChecklists.inicio });
      void cliente.invalidateQueries({ queryKey: ['m', 'checklists', 'lista'] });
      void cliente.invalidateQueries({ queryKey: ['m', 'checklists', 'acompanhamento'] });
    },
  });
}
