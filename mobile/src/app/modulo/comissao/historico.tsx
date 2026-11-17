import { useState } from 'react';
import { View } from 'react-native';

import { Indicador, LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { Barra } from '@/components/modulos/comissao/pecas';
import { Cartao, Entrada, EstadoVazio, Icone, Segmentado, Selo, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useHistoricoComissao } from '@/hooks/modulos/comissao';
import { useCores } from '@/providers/tema';
import type { HistoricoComissao } from '@/services/modulos/comissao';
import { reais } from '@/utils/dinheiro';

type Escopo = 'eu' | 'equipe';

export default function HistoricoComissaoTela() {
  const [escopo, setEscopo] = useState<Escopo>('eu');
  const { data, isPending, error, refetch, isRefetching } = useHistoricoComissao(escopo);

  return (
    <TelaModulo titulo="Histórico" subtitulo="Comissão mês a mês" carregando={isPending} erro={error} aoAtualizar={refetch} atualizando={isRefetching}>
      {data ? (
        <>
          {data.tem_equipe ? (
            <Segmentado
              opcoes={[
                { valor: 'eu', rotulo: 'Eu' },
                { valor: 'equipe', rotulo: 'Equipe' },
              ]}
              valor={escopo}
              aoMudar={setEscopo}
            />
          ) : null}
          {data.meses.length === 0 ? (
            <EstadoVazio icone="time-outline" titulo="Sem histórico ainda" texto="Os meses aparecem aqui conforme as planilhas forem liberadas." />
          ) : (
            <>
              <View className="flex-row flex-wrap gap-3">
                <Indicador rotulo={escopo === 'eu' ? 'Recebido no período' : 'Total da equipe'} valor={reais(data.total.recebido)} icone="wallet-outline" cor={estado.sucesso} />
                <Indicador rotulo="Média por mês" valor={reais(data.media_mes)} icone="analytics-outline" cor={estado.info} />
                {data.melhor_mes ? <Indicador rotulo="Melhor mês" valor={reais(data.melhor_mes.recebido)} detalhe={data.melhor_mes.rotulo} icone="trophy-outline" cor={marca[500]} /> : null}
                {escopo === 'equipe' ? <Indicador rotulo="Pessoas" valor={String(data.n_pessoas)} icone="people-outline" /> : null}
              </View>

              <Texto variante="rotulo" tom="fraco" className="mt-2">
                Meses
              </Texto>
              {data.meses.map((m, i) => (
                <Entrada key={m.chave} indice={i}>
                  <CartaoMes mes={m} partes={data.partes} escopo={escopo} />
                </Entrada>
              ))}

              {escopo === 'equipe' && data.pessoas.length ? (
                <>
                  <Texto variante="rotulo" tom="fraco" className="mt-2">
                    Por pessoa
                  </Texto>
                  <Cartao style={{ paddingHorizontal: 16, paddingVertical: 6 }}>
                    {data.pessoas.map((p) => (
                      <View key={`${p.pessoa}-${p.cargo}-${p.loja}`} className="flex-row items-center gap-3 py-2">
                        <View className="flex-1">
                          <Texto variante="corpoForte" numberOfLines={1}>
                            {p.pessoa}
                          </Texto>
                          <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                            {p.cargo} · {p.loja} · {p.meses_com_valor} {p.meses_com_valor === 1 ? 'mês' : 'meses'}
                          </Texto>
                        </View>
                        <View className="items-end">
                          <Texto variante="corpoForte">{reais(p.total)}</Texto>
                          <Texto variante="legenda" tom="fraco">
                            média {reais(p.media)}
                          </Texto>
                        </View>
                      </View>
                    ))}
                  </Cartao>
                </>
              ) : null}
            </>
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function CartaoMes({ mes: m, partes, escopo }: { mes: HistoricoComissao['meses'][number]; partes: HistoricoComissao['partes']; escopo: Escopo }) {
  const cores = useCores();
  const [aberto, setAberto] = useState(false);
  return (
    <Cartao onPress={() => setAberto((a) => !a)} acessibilidade={`${m.rotulo}: ${reais(m.total.recebido)}`} style={{ padding: 16, gap: 10 }}>
      <View className="flex-row items-center gap-3">
        <View className="flex-1 gap-1">
          <Texto variante="corpoForte">{m.rotulo}</Texto>
          <View className="flex-row flex-wrap gap-2">
            {m.previa ? <Selo texto="Prévia" cor={estado.alerta} /> : null}
            {m.rascunho ? <Selo texto="Rascunho" cor="#6B7280" /> : null}
            {escopo === 'equipe' ? (
              <Texto variante="legenda" tom="fraco">
                {m.pessoas} {m.pessoas === 1 ? 'pessoa' : 'pessoas'}
              </Texto>
            ) : null}
          </View>
        </View>
        <Texto variante="titulo3">{reais(m.total.recebido)}</Texto>
        <Icone nome={aberto ? 'chevron-up' : 'chevron-down'} tamanho={16} cor={cores.texto3} />
      </View>
      <Barra pct={m.pct} cor={estado.sucesso} />
      {aberto ? (
        <View>
          {partes.map((p) =>
            m.total[p.chave] ? <LinhaValor key={p.chave} rotulo={p.rotulo} valor={reais(m.total[p.chave])} cor={p.chave === 'descontos' ? estado.perigo : undefined} /> : null,
          )}
          {escopo === 'eu' && m.linhas.length > 1
            ? m.linhas.map((l) => <LinhaValor key={`${l.pessoa}-${l.fase}-${l.cargo}`} rotulo={`${l.cargo} · ${l.loja} · ${l.fase}`} valor={reais(l.recebido)} />)
            : null}
          <LinhaValor rotulo="Recebido" valor={reais(m.total.recebido)} destaque />
        </View>
      ) : null}
    </Cartao>
  );
}
