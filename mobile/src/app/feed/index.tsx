/** Feed da rede (/social/ do portal): fotos e vídeos de todo mundo, 8 por vez, rolando sem fim. */
import { FlatList, RefreshControl, View } from 'react-native';

import { BotaoPublicar, EsqueletoPostSocial, PostSocial } from '@/components/social/post-social';
import { BarraDetalhe, Entrada, EstadoErro, EstadoVazio, Tela } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useFeedSocial } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';

export default function Feed() {
  const feed = useFeedSocial(8);
  const posts = feed.data?.pages.flatMap((p) => p.resultados) ?? [];

  return (
    <Tela>
      <BarraDetalhe titulo="Feed da rede" />
      {feed.isError && !posts.length ? (
        <EstadoErro mensagem={mensagemDoErro(feed.error)} aoTentar={() => void feed.refetch()} />
      ) : (
        <FlatList
          data={posts}
          keyExtractor={(p) => String(p.id)}
          contentContainerStyle={{ padding: 16, paddingTop: 4, gap: 16, paddingBottom: 48 }}
          ListHeaderComponent={<BotaoPublicar />}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 4 ? index + 1 : 0}>
              <PostSocial post={item} />
            </Entrada>
          )}
          ListEmptyComponent={
            feed.isPending ? (
              <View className="gap-4">
                <EsqueletoPostSocial />
                <EsqueletoPostSocial />
              </View>
            ) : (
              <EstadoVazio icone="camera-outline" titulo="Ninguém postou ainda" texto="Que tal ser o primeiro a compartilhar um momento?" />
            )
          }
          ListFooterComponent={feed.isFetchingNextPage ? <EsqueletoPostSocial /> : undefined}
          onEndReachedThreshold={0.6}
          onEndReached={() => {
            if (feed.hasNextPage && !feed.isFetchingNextPage) void feed.fetchNextPage();
          }}
          refreshControl={
            <RefreshControl refreshing={feed.isRefetching && !feed.isFetchingNextPage} onRefresh={() => void feed.refetch()} tintColor={marca[500]} colors={[marca[500]]} />
          }
        />
      )}
    </Tela>
  );
}
