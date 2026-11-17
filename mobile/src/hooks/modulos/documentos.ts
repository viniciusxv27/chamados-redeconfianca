/** Hooks do módulo Documentos (TanStack Query sobre src/services/modulos/documentos.ts). */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { type DocumentoDetalhe, documentos, type SituacaoDocumentos } from '@/services/modulos/documentos';

export const chavesDocumentos = {
  tudo: ['m', 'documentos'] as const,
  lista: (situacao: SituacaoDocumentos) => ['m', 'documentos', 'lista', situacao] as const,
  detalhe: (id: number) => ['m', 'documentos', 'detalhe', id] as const,
};

export function useDocumentos(situacao: SituacaoDocumentos) {
  return useQuery({ queryKey: chavesDocumentos.lista(situacao), queryFn: () => documentos.listar(situacao), placeholderData: keepPreviousData });
}

/** O detalhe traz links de PDF que valem 5 min: refaz a busca depois de 4 min parado. */
export function useDocumento(id: number) {
  return useQuery({ queryKey: chavesDocumentos.detalhe(id), queryFn: () => documentos.detalhe(id), enabled: id > 0, staleTime: 4 * 60_000 });
}

export function useAssinarDocumento(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (assinatura: string) => documentos.assinar(id, assinatura),
    onSuccess: (detalhe: DocumentoDetalhe) => {
      cliente.setQueryData(chavesDocumentos.detalhe(id), detalhe);
      void cliente.invalidateQueries({ queryKey: ['m', 'documentos', 'lista'] });
    },
  });
}
