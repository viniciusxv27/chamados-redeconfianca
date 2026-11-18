import { type Href, router } from 'expo-router';
import { useMemo, useState } from 'react';
import { FlatList, RefreshControl, ScrollView, View } from 'react-native';

import { AvisoRetirada, CartaoPremio, CartaoSaldo, irParaResgates, VERDE_MERCADINHO } from '@/components/modulos/mercadinho/pecas';
import { useBuscaAdiada } from '@/components/modulos/vendas/tela-lista';
import { BarraDetalhe, BotaoIcone, CabecalhoGrande, Campo, Cartao, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, Tela, Texto } from '@/components/ui';
import { EsqueletoRodape } from '@/components/ui/esqueleto';
import { marca } from '@/constants/theme';
import { useVitrine } from '@/hooks/modulos/mercadinho';
import { mensagemDoErro } from '@/services/api';
import type { OrdemVitrine } from '@/services/modulos/mercadinho';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const ORDENS: { valor: OrdemVitrine; rotulo: string }[] = [
  { valor: 'relevancia', rotulo: 'Mais relevantes' },
  { valor: 'menor_valor', rotulo: 'Menor valor' },
  { valor: 'maior_valor', rotulo: 'Maior valor' },
  { valor: 'recentes', rotulo: 'Mais recentes' },
];

/** Vitrine do Mercadinho: saldo, avisos de retirada, busca, categorias e os prêmios em grade. */
export default function Mercadinho() {
  const { texto, setTexto, busca } = useBuscaAdiada();
  const [categoria, setCategoria] = useState<number | undefined>(undefined);
  const [ordem, setOrdem] = useState<OrdemVitrine>('relevancia');
  const filtro = useMemo(() => ({ busca: busca || undefined, categoria, ordem }), [busca, categoria, ordem]);
  const consulta = useVitrine(filtro);
  const primeira = consulta.data?.pages[0];
  const premios = useMemo(() => consulta.data?.pages.flatMap((p) => p.resultados) ?? [], [consulta.data]);

  const cabecalho = (
    <View style={{ gap: 18, paddingBottom: 4 }}>
      <CartaoSaldo saldo={primeira?.saldo} pendentes={primeira?.pendentes ?? 0} aoVerResgates={irParaResgates} aoVerExtrato={() => void abrirNoPortal('/users/my-cs/')} />
      {(primeira?.aguardando_retirada ?? []).map((r) => (
        <AvisoRetirada key={r.id} premio={r.premio} retirarAte={r.retirar_ate} onPress={irParaResgates} />
      ))}
      <Campo
        icone="search"
        placeholder="Buscar prêmio"
        value={texto}
        onChangeText={setTexto}
        returnKeyType="search"
        autoCorrect={false}
        clearButtonMode="while-editing"
      />
      {primeira?.categorias?.length ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 20 }} style={{ marginHorizontal: -20 }}>
          <Chip rotulo="Todos" ativo={categoria == null} onPress={() => setCategoria(undefined)} />
          {primeira.categorias.map((c) => (
            <Chip key={c.id} rotulo={c.nome} ativo={categoria === c.id} onPress={() => setCategoria(c.id)} />
          ))}
        </ScrollView>
      ) : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 20 }} style={{ marginHorizontal: -20 }}>
        {ORDENS.map((o) => (
          <Chip key={o.valor} rotulo={o.rotulo} ativo={ordem === o.valor} onPress={() => setOrdem(o.valor)} />
        ))}
      </ScrollView>
    </View>
  );

  const rodape = (
    <View style={{ gap: 16, paddingTop: 8 }}>
      {consulta.isFetchingNextPage ? <EsqueletoRodape /> : null}
      <Cartao onPress={() => router.push('/modulo/mercadinho/sugerir' as Href)} acessibilidade="Sugerir um produto" style={{ padding: 18, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
        <View style={{ width: 42, height: 42, borderRadius: 14, backgroundColor: comAlfa(marca[500], 0.12), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome="bulb-outline" tamanho={22} cor={marca[500]} />
        </View>
        <View className="flex-1 gap-0.5">
          <Texto variante="corpoForte">Sugerir um produto</Texto>
          <Texto variante="legenda" tom="suave">
            Mande o link e a empresa avalia se dá para incluir.
          </Texto>
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={marca[500]} />
      </Cartao>
    </View>
  );

  return (
    <Tela>
      <BarraDetalhe
        direita={primeira?.pode_gerenciar ? <BotaoIcone icone="settings-outline" rotulo="Gerenciar no portal" onPress={() => void abrirNoPortal('/prizes/manage/')} /> : undefined}
      />
      {consulta.isPending ? (
        <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
          <CabecalhoGrande titulo="Mercadinho" subtitulo="Troque suas C$" />
          <View className="px-5">
            <EsqueletoLista quantos={4} />
          </View>
        </ScrollView>
      ) : consulta.isError && !premios.length ? (
        <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
          <CabecalhoGrande titulo="Mercadinho" subtitulo="Troque suas C$" />
          <EstadoErro mensagem={mensagemDoErro(consulta.error)} aoTentar={() => void consulta.refetch()} />
        </ScrollView>
      ) : (
        <FlatList
          data={premios}
          keyExtractor={(p) => String(p.id)}
          numColumns={2}
          columnWrapperStyle={{ justifyContent: 'space-between' }}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 10 ? index : 0} style={{ width: '48%', flexGrow: 0 }}>
              <CartaoPremio premio={item} />
            </Entrada>
          )}
          ListHeaderComponent={
            <View style={{ marginHorizontal: -20, paddingBottom: 2 }}>
              <CabecalhoGrande titulo="Mercadinho" subtitulo="Troque suas C$" />
              <View style={{ paddingHorizontal: 20 }}>{cabecalho}</View>
            </View>
          }
          ListFooterComponent={rodape}
          ListEmptyComponent={
            <EstadoVazio
              icone="storefront-outline"
              titulo={busca || categoria != null ? 'Nenhum prêmio encontrado' : 'Vitrine vazia'}
              texto={busca || categoria != null ? 'Tente outro nome ou categoria.' : 'Volte mais tarde: novos prêmios aparecem aqui.'}
            />
          }
          contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 48, gap: 16 }}
          onEndReachedThreshold={0.4}
          onEndReached={() => consulta.hasNextPage && !consulta.isFetchingNextPage && consulta.fetchNextPage()}
          refreshControl={
            <RefreshControl
              refreshing={consulta.isRefetching && !consulta.isFetchingNextPage}
              onRefresh={() => void consulta.refetch()}
              tintColor={VERDE_MERCADINHO}
              colors={[VERDE_MERCADINHO]}
            />
          }
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
        />
      )}
    </Tela>
  );
}
