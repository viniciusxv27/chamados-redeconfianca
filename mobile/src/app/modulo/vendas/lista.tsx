import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { View } from 'react-native';

import { CartaoVenda } from '@/components/modulos/vendas/pecas';
import { TelaLista, useBuscaAdiada } from '@/components/modulos/vendas/tela-lista';
import { Campo, Chip, Segmentado } from '@/components/ui';
import { useVendas } from '@/hooks/modulos/vendas';
import type { Escopo, Periodo } from '@/services/modulos/vendas';

const PERIODOS: { valor: Periodo; rotulo: string }[] = [
  { valor: 'hoje', rotulo: 'Hoje' },
  { valor: 'mes', rotulo: 'Este mês' },
  { valor: 'todos', rotulo: 'Tudo' },
];

const ESCOPOS: { valor: Escopo; rotulo: string }[] = [
  { valor: 'minhas', rotulo: 'Minhas' },
  { valor: 'loja', rotulo: 'Minha loja' },
  { valor: 'rede', rotulo: 'Rede' },
];

/** Todas as vendas que a pessoa enxerga (o recorte é do servidor), com busca e período. */
export default function ListaVendas() {
  const params = useLocalSearchParams<{ periodo?: Periodo; escopo?: Escopo }>();
  const [periodo, setPeriodo] = useState<Periodo>(params.periodo ?? 'mes');
  const [escopo, setEscopo] = useState<Escopo>(params.escopo ?? 'rede');
  const { texto, setTexto, busca } = useBuscaAdiada();
  const filtro = useMemo(() => ({ periodo, escopo, busca: busca || undefined }), [periodo, escopo, busca]);
  const consulta = useVendas(filtro);
  const itens = useMemo(() => consulta.data?.pages.flatMap((p) => p.resultados) ?? [], [consulta.data]);
  const primeira = consulta.data?.pages[0];
  const total = primeira?.total ?? 0;

  return (
    <TelaLista
      titulo="Vendas"
      subtitulo={consulta.isPending ? ' ' : `${total} ${total === 1 ? 'venda' : 'vendas'}`}
      consulta={consulta}
      itens={itens}
      chave={(v) => String(v.id)}
      item={(v) => <CartaoVenda venda={v} />}
      vazio={{
        icone: busca ? 'search-outline' : 'receipt-outline',
        titulo: busca ? 'Nada encontrado' : 'Nenhuma venda',
        texto: busca ? `Nenhuma venda com "${busca}".` : 'Troque o período para ver outras vendas.',
      }}
      filtros={
        <>
          <Campo icone="search" placeholder="Cliente, CPF ou nº da venda" value={texto} onChangeText={setTexto} returnKeyType="search" autoCorrect={false} clearButtonMode="while-editing" />
          <Segmentado opcoes={PERIODOS} valor={periodo} aoMudar={setPeriodo} />
          {primeira?.rede ? (
            <View className="flex-row flex-wrap gap-2">
              {ESCOPOS.map((e) => (
                <Chip key={e.valor} rotulo={e.rotulo} ativo={primeira.escopo === e.valor} onPress={() => setEscopo(e.valor)} />
              ))}
            </View>
          ) : null}
        </>
      }
    />
  );
}
