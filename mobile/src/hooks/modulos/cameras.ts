/** Hooks do módulo Câmeras (TanStack Query sobre src/services/modulos/cameras.ts). */
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';

import { type AlertaDetalhe, cameras, type FiltroAlertas, type FiltroIndicadores, type Tratamento } from '@/services/modulos/cameras';

/**
 * A tela está na frente? A imagem só chega enquanto alguém olha (como na web): a tela
 * pergunta de novo de tempos em tempos só enquanto está visível, e para quando sai.
 */
export function useFocado() {
  const [focado, setFocado] = useState(true);
  useFocusEffect(
    useCallback(() => {
      setFocado(true);
      return () => setFocado(false);
    }, []),
  );
  return focado;
}

export function useInicioCameras(focado: boolean) {
  return useQuery({ queryKey: ['m', 'cameras', 'inicio'], queryFn: cameras.inicio, refetchInterval: focado ? 30_000 : false });
}

/** Na loja a web pergunta pelas fotos a cada 10 s; aqui igual, só com a tela aberta. */
export function useLojaCameras(id: number, focado: boolean) {
  return useQuery({ queryKey: ['m', 'cameras', 'loja', id], queryFn: () => cameras.loja(id), enabled: id > 0, refetchInterval: focado ? 10_000 : false });
}

export function useAlertasCameras(f: FiltroAlertas) {
  return useInfiniteQuery({
    queryKey: ['m', 'cameras', 'alertas', f],
    queryFn: ({ pageParam }) => cameras.alertas(f, pageParam),
    initialPageParam: 1,
    getNextPageParam: (ultima) => (ultima.tem_mais ? ultima.pagina + 1 : undefined),
    placeholderData: keepPreviousData,
  });
}

/** O detalhe traz links que valem 5 min (vídeo 15): refaz a busca depois de 4 min parado. */
export function useAlertaCamera(id: number) {
  return useQuery({ queryKey: ['m', 'cameras', 'alerta', id], queryFn: () => cameras.alerta(id), enabled: id > 0, staleTime: 4 * 60_000 });
}

export function useTratarAlerta(id: number) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (corpo: Tratamento) => cameras.tratar(id, corpo),
    onSuccess: (detalhe: AlertaDetalhe) => {
      cliente.setQueryData(['m', 'cameras', 'alerta', id], detalhe);
      void cliente.invalidateQueries({ queryKey: ['m', 'cameras', 'alertas'] });
      void cliente.invalidateQueries({ queryKey: ['m', 'cameras', 'inicio'] });
      void cliente.invalidateQueries({ queryKey: ['m', 'cameras', 'loja'] });
    },
  });
}

export function useIndicadoresCameras(f: FiltroIndicadores) {
  return useQuery({ queryKey: ['m', 'cameras', 'indicadores', f], queryFn: () => cameras.indicadores(f), placeholderData: keepPreviousData });
}
