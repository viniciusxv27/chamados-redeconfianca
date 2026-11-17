import { useLocalSearchParams } from 'expo-router';
import { Linking, ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { EsqueletoBloco, Indicador, LinhaValor } from '@/components/modulos/comum';
import { Bolinha, CartaoVenda, dataCurta, Secao } from '@/components/modulos/vendas/pecas';
import { BarraDetalhe, Botao, Cartao, EstadoErro, Selo, Tela, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useCliente } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import { reais } from '@/utils/dinheiro';

/** A ficha do cliente: cadastro, plano, compras no Vivo GO e vendas lançadas no portal. */
export default function FichaCliente() {
  const { cpf } = useLocalSearchParams<{ cpf: string }>();
  const { data, isPending, isError, error, refetch } = useCliente(cpf ?? '');
  const cores = useCores();

  return (
    <Tela>
      <BarraDetalhe titulo="Cliente" />
      {isPending ? (
        <View className="gap-4 px-5 pt-2">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 18, paddingBottom: 40 }}>
          <Animated.View entering={FadeInDown.duration(350)}>
            <Cartao style={{ padding: 18, gap: 12 }}>
              <View className="flex-row items-center gap-3">
                <Bolinha icone={data.cpf.length === 14 ? 'business-outline' : 'person-outline'} cor={estado.info} tamanho={48} />
                <View className="flex-1">
                  <Texto variante="titulo3" selectable>
                    {data.nome}
                  </Texto>
                  <Texto variante="legenda" tom="suave" selectable>
                    {data.cpf_formatado}
                  </Texto>
                </View>
              </View>
              <Selo texto={data.origem === 'PORTAL' ? 'Cadastrado no portal' : 'Base do Vivo GO'} cor={cores.texto3} ponto={false} />
              <View>
                {data.telefone ? <LinhaValor rotulo="Telefone" valor={data.telefone} /> : null}
                {data.linha ? <LinhaValor rotulo="Última linha" valor={data.linha} /> : null}
                {data.plano ? <LinhaValor rotulo="Plano" valor={data.plano} /> : null}
                {data.pdv_ultimo ? <LinhaValor rotulo="Último PDV" valor={data.pdv_ultimo} /> : null}
                {data.endereco ? <LinhaValor rotulo="Endereço" valor={data.endereco} /> : null}
              </View>
              {data.telefone ? (
                <Botao titulo="Ligar" variante="secundario" icone="call-outline" tamanho="sm" onPress={() => void Linking.openURL(`tel:${data.telefone?.replace(/\D/g, '')}`)} />
              ) : null}
            </Cartao>
          </Animated.View>

          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo="Compras" valor={String(data.qtd_vendas)} icone="bag-handle-outline" cor={estado.info} />
            <Indicador rotulo="Total gasto" valor={reais(data.total_gasto)} icone="cash-outline" cor={estado.sucesso} />
            <Indicador rotulo="Primeira compra" valor={dataCurta(data.primeira_compra)} icone="flag-outline" cor={cores.texto3} />
            <Indicador rotulo="Última compra" valor={dataCurta(data.ultima_compra)} icone="time-outline" cor={cores.texto3} />
          </View>

          {data.vendas_portal.length ? (
            <View className="gap-2">
              <Texto variante="titulo3">Vendas no portal</Texto>
              {data.vendas_portal.map((v) => (
                <CartaoVenda key={v.id} venda={v} semToque={!v.pode_abrir} />
              ))}
            </View>
          ) : null}

          {data.compras_vivogo.length ? (
            <View className="gap-2">
              <Texto variante="titulo3">Compras no Vivo GO</Texto>
              {data.compras_vivogo.map((c, i) => (
                <Secao key={c.id ?? i} titulo={[dataCurta(c.data), c.pdv].filter(Boolean).join(' · ')} icone="receipt-outline">
                  {c.itens.map((it, j) => (
                    <LinhaValor key={j} rotulo={`${it.item}${it.qtde > 1 ? ` · ${it.qtde}x` : ''} (${it.tipo})`} valor={reais(it.valor)} />
                  ))}
                  {c.vendedor ? (
                    <Texto variante="legenda" tom="fraco" className="pb-2">
                      Vendedor: {c.vendedor}
                    </Texto>
                  ) : null}
                </Secao>
              ))}
            </View>
          ) : null}
        </ScrollView>
      )}
    </Tela>
  );
}
