import { useMemo, useState } from 'react';
import { ScrollView } from 'react-native';

import { CartaoProduto } from '@/components/modulos/vendas/pecas';
import { TelaLista, useBuscaAdiada } from '@/components/modulos/vendas/tela-lista';
import { Campo, Chip } from '@/components/ui';
import { usePrecos } from '@/hooks/modulos/vendas';

/** Tabela de preços no balcão: busca por aparelho, marca ou SKU, e as abas do catálogo. */
export default function TabelaDePrecos() {
  const [categoria, setCategoria] = useState('');
  const { texto, setTexto, busca } = useBuscaAdiada();
  const filtro = useMemo(() => ({ busca: busca || undefined, categoria: categoria || undefined }), [busca, categoria]);
  const consulta = usePrecos(filtro);
  const itens = useMemo(() => consulta.data?.pages.flatMap((p) => p.resultados) ?? [], [consulta.data]);
  const primeira = consulta.data?.pages[0];
  const abas = primeira?.abas ?? [];
  const total = primeira?.total ?? 0;

  return (
    <TelaLista
      titulo="Tabela de preços"
      subtitulo={consulta.isPending ? ' ' : `${total} ${total === 1 ? 'produto' : 'produtos'}`}
      consulta={consulta}
      itens={itens}
      chave={(p) => String(p.id)}
      item={(p) => <CartaoProduto produto={p} />}
      vazio={{
        icone: 'search-outline',
        titulo: busca ? 'Nada encontrado' : 'Tabela vazia',
        texto: busca ? `Nenhum produto com "${busca}". Tente o modelo, a marca ou o SKU.` : 'Nenhum produto nesta aba.',
      }}
      filtros={
        <>
          <Campo
            icone="search"
            placeholder="Aparelho, marca ou SKU"
            value={texto}
            onChangeText={setTexto}
            returnKeyType="search"
            autoCorrect={false}
            autoCapitalize="none"
            clearButtonMode="while-editing"
          />
          {abas.length ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
              <Chip rotulo="Todos" ativo={!categoria} onPress={() => setCategoria('')} />
              {abas.map((a) => (
                <Chip key={a.valor} rotulo={`${a.rotulo} · ${a.n}`} ativo={categoria === a.valor} onPress={() => setCategoria(a.valor)} />
              ))}
            </ScrollView>
          ) : null}
        </>
      }
    />
  );
}
