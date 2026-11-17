import { router, type Href } from 'expo-router';
import { useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, View } from 'react-native';

import { CartaoSolicitacao } from '@/components/modulos/ponto/pecas';
import { BarraDetalhe, BotaoIcone, CabecalhoGrande, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Segmentado, Tela } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useSolicitacoes } from '@/hooks/modulos/ponto';
import { mensagemDoErro } from '@/services/api';
import type { FiltroSolicitacoes, SolicitacaoResumo } from '@/services/modulos/ponto';

/** Minhas solicitações ao DP (o mesmo recorte de /ponto/solicitacoes/: só as da própria pessoa). */
export default function MinhasSolicitacoes() {
  const [filtro, setFiltro] = useState<FiltroSolicitacoes>('abertas');
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useSolicitacoes(filtro);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const contagem = data?.pages[0]?.contagem;

  return (
    <Tela>
      <BarraDetalhe
        direita={<BotaoIcone icone="add" rotulo="Nova solicitação" onPress={() => router.push('/modulo/ponto/nova' as Href)} />}
      />
      <FlatList<SolicitacaoResumo>
        data={isPending || (isError && !itens.length) ? [] : itens}
        keyExtractor={(s) => String(s.id)}
        ListHeaderComponent={
          <View className="gap-3 pb-3">
            <CabecalhoGrande
              titulo="Solicitações"
              subtitulo={contagem ? `${contagem.abertas} em andamento · ${contagem.total} no total` : 'Ao DP'}
            />
            <View className="px-5">
              <Segmentado
                opcoes={[
                  { valor: 'abertas', rotulo: 'Em andamento' },
                  { valor: 'APROVADA', rotulo: 'Aprovadas' },
                  { valor: '', rotulo: 'Todas' },
                ]}
                valor={filtro}
                aoMudar={setFiltro}
              />
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
            <CartaoSolicitacao sol={item} />
          </Entrada>
        )}
        contentContainerStyle={{ paddingBottom: 40, gap: 12 }}
        onEndReachedThreshold={0.4}
        onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
        refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
        ListFooterComponent={isFetchingNextPage ? <ActivityIndicator color={marca[500]} style={{ marginVertical: 16 }} /> : undefined}
        ListEmptyComponent={
          isPending || isError ? undefined : (
            <EstadoVazio
              icone="document-text-outline"
              titulo={filtro === 'abertas' ? 'Nada em andamento' : 'Nenhuma solicitação'}
              texto="Peça ajuste de ponto ou envie atestado e declaração pelo botão +."
            />
          )
        }
      />
    </Tela>
  );
}
