import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, RefreshControl, View } from 'react-native';
import Animated, { useAnimatedScrollHandler, useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CartaoChamado } from '@/components/chamados/cartao-chamado';
import { CabecalhoGrande, Campo, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, Pressionavel, Segmentado, Tela, Texto } from '@/components/ui';
import { marca, movimento, raios } from '@/constants/theme';
import { useChamados } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import type { FiltroChamados } from '@/services/portal';
import type { ChamadoResumo } from '@/services/tipos';

const ESCOPOS: { valor: FiltroChamados['escopo']; rotulo: string }[] = [
  { valor: 'todos', rotulo: 'Todos' },
  { valor: 'comigo', rotulo: 'Comigo' },
  { valor: 'meus', rotulo: 'Abertos por mim' },
];

export default function Chamados() {
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ escopo?: FiltroChamados['escopo'] }>();
  const [situacao, setSituacao] = useState<FiltroChamados['situacao']>('abertos');
  const [escopo, setEscopo] = useState<FiltroChamados['escopo']>(params.escopo ?? 'todos');
  const [texto, setTexto] = useState('');
  const [busca, setBusca] = useState('');

  // Os atalhos do Início chegam com ?escopo=… (ajuste durante o render, sem efeito).
  const [escopoDoLink, setEscopoDoLink] = useState(params.escopo);
  if (params.escopo !== escopoDoLink) {
    setEscopoDoLink(params.escopo);
    if (params.escopo) setEscopo(params.escopo);
  }

  // Busca espera a pessoa parar de digitar (350 ms).
  useEffect(() => {
    const t = setTimeout(() => setBusca(texto.trim()), 350);
    return () => clearTimeout(t);
  }, [texto]);

  const filtro = useMemo(() => ({ situacao, escopo, busca: busca || undefined }), [situacao, escopo, busca]);
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useChamados(filtro);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const total = data?.pages[0]?.total ?? 0;

  // O botão "Novo chamado" vira só o "+" quando a pessoa rola para baixo.
  const recolhido = useSharedValue(0);
  const ultimoY = useSharedValue(0);
  const aoRolar = useAnimatedScrollHandler((e) => {
    const y = e.contentOffset.y;
    recolhido.set(withSpring(y > 80 && y > ultimoY.get() ? 1 : 0, movimento.mola));
    ultimoY.set(y);
  });
  const botao = useAnimatedStyle(() => ({ width: 56 + (1 - recolhido.get()) * 128 }));
  const rotulo = useAnimatedStyle(() => ({ opacity: 1 - recolhido.get() * 1.6 }));

  return (
    <Tela topo>
      <CabecalhoGrande titulo="Chamados" subtitulo={isPending ? ' ' : `${total} ${total === 1 ? 'chamado' : 'chamados'}`} />
      <View className="gap-3 px-5 pb-3">
        <Campo icone="search" placeholder="Buscar por título, texto ou #número" value={texto} onChangeText={setTexto} returnKeyType="search" autoCorrect={false} clearButtonMode="while-editing" />
        <Segmentado
          opcoes={[
            { valor: 'abertos', rotulo: 'Em aberto' },
            { valor: 'concluidos', rotulo: 'Concluídos' },
            { valor: 'todos', rotulo: 'Todos' },
          ]}
          valor={situacao}
          aoMudar={setSituacao}
        />
        <View className="flex-row gap-2">
          {ESCOPOS.map((e) => (
            <Chip key={e.valor} rotulo={e.rotulo} ativo={escopo === e.valor} onPress={() => setEscopo(e.valor)} />
          ))}
        </View>
      </View>

      {isPending ? (
        <View className="px-5 pt-1">
          <EsqueletoLista quantos={4} />
        </View>
      ) : isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <Animated.FlatList<ChamadoResumo>
          data={itens}
          keyExtractor={(c) => String(c.id)}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 10 ? index : 0}>
              <CartaoChamado chamado={item} />
            </Entrada>
          )}
          onScroll={aoRolar}
          scrollEventThrottle={16}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 110, gap: 12 }}
          onEndReachedThreshold={0.4}
          onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
          refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          ListFooterComponent={isFetchingNextPage ? <ActivityIndicator color={marca[500]} style={{ marginVertical: 16 }} /> : undefined}
          ListEmptyComponent={
            <EstadoVazio
              icone={busca ? 'search-outline' : 'checkmark-done-circle-outline'}
              titulo={busca ? 'Nada encontrado' : situacao === 'abertos' ? 'Tudo em dia por aqui' : 'Nenhum chamado'}
              texto={busca ? `Nenhum chamado com "${busca}".` : 'Quando houver chamados neste filtro, eles aparecem aqui.'}
            />
          }
          keyboardDismissMode="on-drag"
        />
      )}

      <Animated.View style={[{ position: 'absolute', right: 20, bottom: insets.bottom > 0 ? 16 : 20, height: 56, borderRadius: raios.pilula, overflow: 'hidden', boxShadow: '0px 10px 24px rgba(255,107,53,0.45)' }, botao]}>
        <Pressionavel
          onPress={() => router.push('/chamado/novo')}
          vibrar
          escala={0.92}
          accessibilityLabel="Abrir novo chamado"
          style={{ flex: 1, backgroundColor: marca[500], flexDirection: 'row', alignItems: 'center', paddingLeft: 16, gap: 8 }}>
          <Icone nome="add" tamanho={24} cor="#fff" />
          <Animated.View style={rotulo}>
            <Texto variante="corpoForte" tom="branco" numberOfLines={1}>
              Novo chamado
            </Texto>
          </Animated.View>
        </Pressionavel>
      </Animated.View>
    </Tela>
  );
}
