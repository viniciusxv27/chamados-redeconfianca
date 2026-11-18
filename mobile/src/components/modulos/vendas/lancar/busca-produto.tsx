/** Folha de busca de produto: a mesma busca da tabela de preços, com o preço sugerido pelo plano do cliente. */
import { FlatList, KeyboardAvoidingView, Modal, Platform, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useBuscaAdiada } from '@/components/modulos/vendas/tela-lista';
import { BotaoIcone, Campo, Cartao, EsqueletoLista, EstadoErro, EstadoVazio, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useProdutosVenda } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ProdutoVenda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

export function BuscaProduto({ aberto, segmentacao, aoFechar, aoEscolher }: { aberto: boolean; segmentacao: string; aoFechar: () => void; aoEscolher: (p: ProdutoVenda) => void }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const { texto, setTexto, busca } = useBuscaAdiada();
  const consulta = useProdutosVenda(busca, segmentacao);
  const itens = consulta.data ?? [];

  return (
    <Modal visible={aberto} animationType="slide" presentationStyle="pageSheet" onRequestClose={aoFechar}>
      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ backgroundColor: cores.fundo }}>
        <View className="flex-row items-center gap-3 px-5 pb-3" style={{ paddingTop: Platform.OS === 'ios' ? 16 : insets.top + 12 }}>
          <Texto variante="titulo2" className="flex-1">
            Adicionar produto
          </Texto>
          <BotaoIcone icone="close" rotulo="Fechar" onPress={aoFechar} />
        </View>
        <View className="px-5 pb-3">
          <Campo icone="search" placeholder="Aparelho, marca ou SKU" value={texto} onChangeText={setTexto} autoFocus autoCorrect={false} autoCapitalize="none" returnKeyType="search" />
        </View>
        {busca.length < 2 ? (
          <EstadoVazio icone="search-outline" titulo="Busque o produto" texto="Digite ao menos 2 letras do nome, da marca ou o SKU inteiro." />
        ) : consulta.isPending ? (
          <View className="px-5">
            <EsqueletoLista quantos={4} />
          </View>
        ) : consulta.isError ? (
          <EstadoErro mensagem={mensagemDoErro(consulta.error)} aoTentar={() => void consulta.refetch()} />
        ) : (
          <FlatList
            data={itens}
            keyExtractor={(p) => String(p.id)}
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 40, gap: 10 }}
            ListEmptyComponent={<EstadoVazio icone="search-outline" titulo="Nada encontrado" texto={`Nenhum produto com "${busca}".`} />}
            renderItem={({ item: p }) => (
              <Cartao onPress={() => aoEscolher(p)} acessibilidade={`Adicionar ${p.nome}`} style={{ padding: 14, gap: 6 }}>
                <View className="flex-row items-start gap-3">
                  <View className="flex-1 gap-0.5">
                    <Texto variante="corpoForte" numberOfLines={2}>
                      {p.nome}
                    </Texto>
                    <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                      {[p.categoria_slv, p.tipo || p.categoria, p.fabricante].filter(Boolean).join(' · ')}
                    </Texto>
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <Texto variante="corpoForte">{p.valor_sugerido ? reais(p.valor_sugerido) : '—'}</Texto>
                    <Texto variante="legenda" tom="fraco" numberOfLines={1} style={{ maxWidth: 140 }}>
                      {p.regra}
                    </Texto>
                  </View>
                </View>
                <View className="flex-row flex-wrap gap-2">
                  {p.sku_digitado ? <Selo texto={`SKU ${p.sku}${p.cor ? ` · ${p.cor}` : ''}`} cor={estado.info} /> : null}
                  {p.grupos.length ? <Selo texto={`${p.grupos.length} grupos de plano`} cor={cores.texto3} ponto={false} /> : null}
                  {p.portfolio && p.portfolio !== 'In' ? <Selo texto={p.portfolio} cor={estado.alerta} ponto={false} /> : null}
                </View>
              </Cartao>
            )}
          />
        )}
      </KeyboardAvoidingView>
    </Modal>
  );
}
