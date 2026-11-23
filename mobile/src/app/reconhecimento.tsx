/**
 * Mural de Reconhecimento — o /compliments/mural/ do portal: todos os reconhecimentos da rede,
 * com os números do topo (total, no mês, para mim), o filtro Todos / Para mim / Meu setor /
 * Que eu fiz, a busca e "carregar mais" (24 por vez).
 */
import { LinearGradient } from 'expo-linear-gradient';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { COR_RECONHECIMENTO, CartaoReconhecimento } from '@/components/elogios/cartao-reconhecimento';
import { BarraDetalhe, BotaoIcone, Campo, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, Tela, Texto } from '@/components/ui';
import { EsqueletoRodape } from '@/components/ui/esqueleto';
import { fontes, marca, raios } from '@/constants/theme';
import { useReconhecimentos } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import type { FiltroMural } from '@/services/tipos';
import { abrirNoPortal } from '@/utils/links';

const FILTROS: { valor: FiltroMural; rotulo: string }[] = [
  { valor: '', rotulo: 'Todos' },
  { valor: 'para_mim', rotulo: 'Para mim' },
  { valor: 'meu_setor', rotulo: 'Meu setor' },
  { valor: 'que_fiz', rotulo: 'Que eu fiz' },
];

export default function MuralReconhecimento() {
  const [ver, setVer] = useState<FiltroMural>('');
  const [digitado, setDigitado] = useState('');
  const [busca, setBusca] = useState('');
  // A busca vai ao portal depois de uma pausa na digitação.
  useEffect(() => {
    const t = setTimeout(() => setBusca(digitado.trim()), 400);
    return () => clearTimeout(t);
  }, [digitado]);

  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage, isFetching } = useReconhecimentos(ver, busca);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const primeira = data?.pages[0];
  const numeros = primeira?.numeros;
  const filtros = primeira?.filtros ?? FILTROS;

  const cabecalho = (
    <View style={{ gap: 16, paddingBottom: 4 }}>
      <LinearGradient colors={[...COR_RECONHECIMENTO]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ borderRadius: raios['2xl'], padding: 18, gap: 14 }}>
        <View className="flex-row items-center gap-3">
          <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: 'rgba(255,255,255,0.22)', alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome="ribbon" tamanho={22} cor="#FFFFFF" />
          </View>
          <View className="flex-1">
            <Texto variante="titulo2" tom="branco">
              Reconhecimento
            </Texto>
            <Texto variante="legenda" style={{ color: 'rgba(255,255,255,0.9)' }}>
              Quem fez a diferença na rede
            </Texto>
          </View>
        </View>
        <View className="flex-row gap-2">
          <Numero valor={numeros?.total} rotulo="no mural" />
          <Numero valor={numeros?.no_mes} rotulo="neste mês" />
          <Numero valor={numeros?.para_mim} rotulo="para mim" />
        </View>
      </LinearGradient>
      <View className="flex-row flex-wrap gap-2">
        {filtros.map((f) => (
          <Chip key={f.valor || 'todos'} rotulo={f.rotulo} ativo={ver === f.valor} onPress={() => setVer(f.valor)} />
        ))}
      </View>
      <Campo icone="search" placeholder="Buscar pessoa, setor ou texto…" value={digitado} onChangeText={setDigitado} autoCorrect={false} returnKeyType="search" />
      {primeira && (ver || busca) ? (
        <Texto variante="legenda" tom="fraco">
          {primeira.total === 1 ? '1 reconhecimento' : `${primeira.total} reconhecimentos`}
          {isFetching && !isFetchingNextPage ? ' · buscando…' : ''}
        </Texto>
      ) : null}
    </View>
  );

  return (
    <Tela>
      <BarraDetalhe titulo="Mural de Reconhecimento" direita={<BotaoIcone icone="heart-outline" rotulo="Reconhecer alguém no portal" onPress={() => abrirNoPortal('/compliments/create/')} />} />
      {isPending ? (
        <View style={{ paddingHorizontal: 20, gap: 16 }}>
          {cabecalho}
          <EsqueletoLista quantos={3} />
        </View>
      ) : isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <FlatList
          data={itens}
          keyExtractor={(e) => String(e.id)}
          ListHeaderComponent={cabecalho}
          keyboardShouldPersistTaps="handled"
          renderItem={({ item, index }) => (
            <Entrada indice={index < 6 ? index : 0}>
              <CartaoReconhecimento elogio={item} />
            </Entrada>
          )}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 40, gap: 14 }}
          onEndReachedThreshold={0.5}
          onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
          refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
          ListEmptyComponent={
            <EstadoVazio
              icone="ribbon-outline"
              titulo={ver || busca ? 'Nada encontrado' : 'Ninguém foi reconhecido ainda'}
              texto={ver || busca ? 'Tente outro filtro ou outra busca.' : 'Seja o primeiro a reconhecer o trabalho de um colega.'}
            />
          }
        />
      )}
    </Tela>
  );
}

function Numero({ valor, rotulo }: { valor: number | undefined; rotulo: string }) {
  return (
    <View style={{ flex: 1, borderRadius: raios.md, backgroundColor: 'rgba(255,255,255,0.18)', paddingVertical: 10, alignItems: 'center' }}>
      <Texto tom="branco" style={{ fontFamily: fontes.negrito, fontSize: 20, lineHeight: 26 }}>
        {valor == null ? '–' : String(valor)}
      </Texto>
      <Texto variante="legenda" style={{ color: 'rgba(255,255,255,0.9)', fontSize: 12 }}>
        {rotulo}
      </Texto>
    </View>
  );
}
