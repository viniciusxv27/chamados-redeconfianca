import { useLocalSearchParams } from 'expo-router';
import { ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { EsqueletoBloco, LinhaValor } from '@/components/modulos/comum';
import { COR_VENDAS, Secao } from '@/components/modulos/vendas/pecas';
import { BarraDetalhe, BotaoIcone, Cartao, EstadoErro, Selo, Tela, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useVenda } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { Venda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';
import { dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

/** Detalhe da venda: cliente, produtos (SKU, cor, IMEI), serviços, pagamento e histórico. */
export default function DetalheVenda() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, isPending, isError, error, refetch } = useVenda(Number(id));
  const cores = useCores();

  return (
    <Tela>
      <BarraDetalhe
        titulo={`Venda #${id}`}
        direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => void abrirNoPortal(data.url_web)} /> : undefined}
      />
      {isPending ? (
        <View className="gap-4 px-5 pt-2">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 18, paddingBottom: 40 }}>
          <Cabecalho venda={data} />
          {data.produtos.length ? (
            <Secao titulo={`Produtos (${data.produtos.length})`} icone="phone-portrait-outline">
              {data.produtos.map((p, i) => (
                <View key={p.id} className="gap-1 py-3" style={i ? { borderTopWidth: 1, borderTopColor: cores.bordaSuave } : undefined}>
                  <View className="flex-row items-start gap-3">
                    <Texto variante="corpoForte" className="flex-1">
                      {p.nome}
                    </Texto>
                    <Texto variante="corpoForte">{reais(p.valor)}</Texto>
                  </View>
                  <Texto variante="legenda" tom="suave" selectable>
                    {[p.sku ? `SKU ${p.sku}` : 'Sem SKU', p.cor, p.serial ? `Serial ${p.serial}` : p.prateleira_infinita ? 'Prateleira infinita' : null]
                      .filter(Boolean)
                      .join(' · ')}
                  </Texto>
                  {p.regra ? (
                    <Texto variante="legenda" tom="fraco">
                      {p.regra}
                      {p.valor_sugerido != null && p.valor_sugerido !== p.valor ? ` · tabela ${reais(p.valor_sugerido)}` : ''}
                    </Texto>
                  ) : null}
                  <View className="flex-row flex-wrap gap-2">
                    {p.valor_editado ? <Selo texto="Valor editado" cor={estado.alerta} /> : null}
                    {p.renova.map((r) => (
                      <Selo key={r} texto={r} cor={estado.sucesso} ponto={false} />
                    ))}
                  </View>
                </View>
              ))}
            </Secao>
          ) : null}
          {data.servicos.length ? (
            <Secao titulo={`Serviços (${data.servicos.length})`} icone="cellular-outline">
              {data.servicos.map((s, i) => (
                <View key={s.id} className="gap-1 py-3" style={i ? { borderTopWidth: 1, borderTopColor: cores.bordaSuave } : undefined}>
                  <View className="flex-row items-start gap-3">
                    <Texto variante="corpoForte" className="flex-1">
                      {s.servico}
                      {s.plano ? ` · ${s.plano}` : ''}
                    </Texto>
                    <Texto variante="corpoForte" style={{ color: '#DB2777' }}>
                      {reais(s.valor)}/mês
                    </Texto>
                  </View>
                  <Texto variante="legenda" tom="suave">
                    {[s.linha ? `Linha ${s.linha}` : null, s.plano_anterior ? `antes: ${s.plano_anterior}` : null, s.status].filter(Boolean).join(' · ')}
                  </Texto>
                  <View className="flex-row flex-wrap gap-2">
                    {s.delta != null ? <Selo texto={`Delta ${reais(s.delta)}`} cor={s.delta >= 0 ? estado.sucesso : estado.perigo} ponto={false} /> : null}
                    {s.portabilidade ? <Selo texto="Portabilidade" cor={estado.info} ponto={false} /> : null}
                    {s.numero_fake ? <Selo texto="Número fictício" cor={estado.perigo} /> : null}
                  </View>
                </View>
              ))}
            </Secao>
          ) : null}
          {data.pagamentos.length || data.desconto_pix || data.vivo_mais ? (
            <Secao titulo="Pagamento" icone="card-outline">
              {data.pagamentos.map((pg, i) => (
                <LinhaValor key={i} rotulo={pg.parcelas ? `${pg.forma} · ${pg.parcelas}x` : pg.forma} valor={reais(pg.valor)} />
              ))}
              {data.desconto_pix ? <LinhaValor rotulo="Desconto no Pix" valor={`− ${reais(data.desconto_pix)}`} cor={estado.sucesso} /> : null}
              {data.vivo_mais ? <LinhaValor rotulo="Pago pelo Vivo+" valor={data.vivo_mais_percentual != null ? `${data.vivo_mais_percentual}%` : 'Sim'} /> : null}
              <LinhaValor rotulo="Total dos produtos" valor={reais(data.total)} destaque />
            </Secao>
          ) : null}
          {data.observacao ? (
            <Secao titulo="Observação" icone="chatbox-ellipses-outline">
              <Texto className="py-2" selectable>
                {data.observacao}
              </Texto>
            </Secao>
          ) : null}
          {data.historico?.length ? (
            <Secao titulo="Alterações" icone="time-outline">
              {data.historico.map((h, i) => (
                <View key={i} className="gap-1 py-3">
                  <Texto variante="legenda" tom="fraco">
                    {dataHora(h.quando)} · {h.por}
                  </Texto>
                  {h.motivo ? <Texto variante="legenda">{h.motivo}</Texto> : null}
                  {h.linhas.map((l, j) => (
                    <Texto key={j} variante="legenda" tom="suave">
                      {l.item ? `${l.item} · ` : ''}
                      {l.campo}: {l.antes || '—'} → {l.depois || '—'}
                    </Texto>
                  ))}
                </View>
              ))}
            </Secao>
          ) : null}
        </ScrollView>
      )}
    </Tela>
  );
}

function Cabecalho({ venda }: { venda: Venda }) {
  const cores = useCores();
  return (
    <Animated.View entering={FadeInDown.duration(350)}>
      <Cartao style={{ padding: 18, gap: 12 }}>
        <View className="flex-row flex-wrap gap-2">
          {venda.tipo_venda ? <Selo texto={venda.tipo_venda} cor={COR_VENDAS} ponto={false} /> : null}
          <Selo texto={venda.comprovante} cor={cores.texto3} ponto={false} />
          {venda.numero_fake ? <Selo texto="Número fictício" cor={estado.perigo} /> : null}
        </View>
        <View>
          <Texto variante="titulo2" selectable>
            {venda.cliente}
          </Texto>
          <Texto variante="legenda" tom="suave" selectable>
            {[venda.cliente_cpf, venda.cliente_telefone].filter(Boolean).join(' · ') || 'Sem documento'}
          </Texto>
        </View>
        <Texto variante="display" style={{ fontFamily: fontes.extranegrito }}>
          {reais(venda.total)}
        </Texto>
        <View>
          <LinhaValor rotulo="Data" valor={dataHora(venda.data)} />
          <LinhaValor rotulo="Loja" valor={venda.loja ?? '—'} />
          <LinhaValor rotulo="Vendedor" valor={venda.vendedor ?? '—'} />
          {venda.lancada_por && venda.lancada_por !== venda.vendedor ? <LinhaValor rotulo="Lançada por" valor={venda.lancada_por} /> : null}
          {venda.plano_anterior ? <LinhaValor rotulo="Plano anterior" valor={venda.plano_anterior} /> : null}
        </View>
      </Cartao>
    </Animated.View>
  );
}
