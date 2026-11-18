import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { CartaoExecucao } from '@/components/modulos/checklists/pecas';
import { BarraDetalhe, CabecalhoGrande, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Segmentado, Tela } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useExecucoes } from '@/hooks/modulos/checklists';
import { mensagemDoErro } from '@/services/api';
import type { ExecucaoResumo, FiltroExecucoes } from '@/services/modulos/checklists';
import { EsqueletoRodape } from '@/components/ui/esqueleto';

const FILTROS: { valor: FiltroExecucoes; rotulo: string }[] = [
  { valor: 'pendentes', rotulo: 'Pendentes' },
  { valor: 'enviados', rotulo: 'Enviados' },
  { valor: 'todos', rotulo: 'Todos' },
];

const VAZIO: Record<FiltroExecucoes, { titulo: string; texto: string }> = {
  pendentes: { titulo: 'Nada pendente', texto: 'Tudo o que foi atribuído para você até hoje já foi enviado.' },
  enviados: { titulo: 'Nenhum enviado ainda', texto: 'Os checklists que você enviar aparecem aqui.' },
  todos: { titulo: 'Nenhum checklist', texto: 'Quando alguém atribuir um checklist para você, ele aparece aqui.' },
};

/** Meus checklists (só os atribuídos à própria pessoa, como /checklists/my/ no portal), do mais novo para o mais antigo. */
export default function MeusChecklists() {
  const params = useLocalSearchParams<{ filtro?: string }>();
  const inicial = FILTROS.some((f) => f.valor === params.filtro) ? (params.filtro as FiltroExecucoes) : 'pendentes';
  const [filtro, setFiltro] = useState<FiltroExecucoes>(inicial);
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useExecucoes(filtro);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const total = data?.pages[0]?.total;

  return (
    <Tela>
      <BarraDetalhe />
      <FlatList<ExecucaoResumo>
        data={isPending || (isError && !itens.length) ? [] : itens}
        keyExtractor={(e) => String(e.id)}
        ListHeaderComponent={
          <View className="gap-3 pb-3">
            <CabecalhoGrande titulo="Meus checklists" subtitulo={total != null ? `${total} ${total === 1 ? 'checklist' : 'checklists'}` : 'Checklists ADM'} />
            <View className="px-5">
              <Segmentado opcoes={FILTROS} valor={filtro} aoMudar={setFiltro} />
            </View>
            {isPending ? (
              <View className="px-5">
                <EsqueletoLista quantos={4} />
              </View>
            ) : isError && !itens.length ? (
              <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
            ) : null}
          </View>
        }
        renderItem={({ item, index }) => (
          <Entrada indice={index < 10 ? index : 0} style={{ paddingHorizontal: 20 }}>
            <CartaoExecucao e={item} />
          </Entrada>
        )}
        contentContainerStyle={{ paddingBottom: 40, gap: 12 }}
        onEndReachedThreshold={0.4}
        onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
        refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
        ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
        ListEmptyComponent={isPending || isError ? undefined : <EstadoVazio icone="checkbox-outline" titulo={VAZIO[filtro].titulo} texto={VAZIO[filtro].texto} />}
      />
    </Tela>
  );
}
