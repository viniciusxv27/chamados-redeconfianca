import { type Href, router } from 'expo-router';
import { useMemo, useState } from 'react';
import { View } from 'react-native';

import { EsqueletoBloco, Indicador, LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { Bolinha, Barras, CartaoVenda, COR_VENDAS, Secao, VERDE_WHATSAPP } from '@/components/modulos/vendas/pecas';
import { Cartao, Chip, Entrada, EstadoVazio, Icone, type NomeIcone, Pressionavel, Segmentado, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useResumoVendas, useVendas } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import type { Escopo, Periodo } from '@/services/modulos/vendas';
import { numero, reais } from '@/utils/dinheiro';
import { tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

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

/** Entrada de Vendas: atalhos do balcão, números do período e as últimas vendas. */
export default function InicioVendas() {
  const [periodo, setPeriodo] = useState<Periodo>('mes');
  const [escopo, setEscopo] = useState<Escopo>('rede');
  const filtro = useMemo(() => ({ periodo, escopo }), [periodo, escopo]);
  const resumo = useResumoVendas(filtro);
  const lista = useVendas(filtro);
  const d = resumo.data;
  const ultimas = lista.data?.pages[0]?.resultados.slice(0, 5) ?? [];

  return (
    <TelaModulo
      titulo="Vendas"
      subtitulo={d?.loja ?? 'Rede Confiança'}
      carregando={resumo.isPending}
      erro={resumo.isError && !d ? resumo.error : undefined}
      aoAtualizar={() => {
        void resumo.refetch();
        void lista.refetch();
      }}
      atualizando={resumo.isRefetching}>
      {d ? (
        <>
          <Atalhos pode={d.pode} tabela={d.tabela_atualizada_em} />

          <View className="gap-3">
            <Segmentado opcoes={PERIODOS} valor={periodo} aoMudar={setPeriodo} />
            {d.pode.rede ? (
              <View className="flex-row flex-wrap gap-2">
                {ESCOPOS.map((e) => (
                  <Chip key={e.valor} rotulo={e.rotulo} ativo={d.escopo === e.valor} onPress={() => setEscopo(e.valor)} />
                ))}
              </View>
            ) : null}
          </View>

          <View className="flex-row flex-wrap gap-3" style={{ opacity: resumo.isPlaceholderData ? 0.5 : 1 }}>
            <Indicador rotulo="Vendido em produtos" valor={reais(d.indicadores.total)} icone="cash-outline" cor={marca[500]} />
            <Indicador rotulo="Vendas" valor={numero(d.indicadores.quantidade)} detalhe={`${numero(d.indicadores.pecas)} peças`} icone="receipt-outline" cor={COR_VENDAS} />
            <Indicador rotulo="Ticket médio" valor={reais(d.indicadores.ticket)} icone="trending-up-outline" cor={estado.sucesso} />
            <Indicador
              rotulo="Planos (mensalidade)"
              valor={reais(d.indicadores.servicos)}
              detalhe={d.indicadores.delta ? `Delta ${reais(d.indicadores.delta)}` : undefined}
              icone="cellular-outline"
              cor="#DB2777"
            />
          </View>
          {d.indicadores.fake ? (
            <Cartao style={{ padding: 14, flexDirection: 'row', gap: 10, alignItems: 'center' }}>
              <Icone nome="warning-outline" tamanho={20} cor={estado.perigo} />
              <Texto variante="legenda" className="flex-1">
                {d.indicadores.fake} {d.indicadores.fake === 1 ? 'venda tem' : 'vendas têm'} número fictício no período.
              </Texto>
            </Cartao>
          ) : null}

          <Secao titulo="Vendas por dia · 14 dias" icone="bar-chart-outline">
            <Barras serie={d.serie} />
          </Secao>

          {d.top_produtos.length ? (
            <Secao titulo="Mais vendidos" icone="trophy-outline">
              {d.top_produtos.map((p) => (
                <LinhaValor key={p.nome} rotulo={`${p.nome} · ${p.n} un.`} valor={reais(p.total)} />
              ))}
            </Secao>
          ) : null}

          {d.por_tipo_servico.length ? (
            <Secao titulo="Serviços" icone="cellular-outline">
              {d.por_tipo_servico.map((s) => (
                <LinhaValor key={s.tipo} rotulo={s.tipo} valor={`${s.n} · delta ${reais(s.delta)}`} />
              ))}
            </Secao>
          ) : null}

          {d.por_loja?.length ? <Ranking titulo="Por loja" icone="storefront-outline" linhas={d.por_loja} /> : null}
          {d.por_vendedor?.length ? <Ranking titulo="Por vendedor" icone="people-outline" linhas={d.por_vendedor} /> : null}

          <View className="gap-2">
            <View className="flex-row items-center justify-between px-1">
              <Texto variante="titulo3">Últimas vendas</Texto>
              <Pressionavel onPress={() => router.push({ pathname: '/modulo/vendas/lista', params: { periodo, escopo } } as unknown as Href)} hitSlop={8}>
                <Texto variante="corpoForte" tom="marca">
                  Ver todas
                </Texto>
              </Pressionavel>
            </View>
            {lista.isPending ? (
              <EsqueletoBloco linhas={3} />
            ) : ultimas.length ? (
              ultimas.map((v, i) => (
                <Entrada key={v.id} indice={i}>
                  <CartaoVenda venda={v} />
                </Entrada>
              ))
            ) : (
              <EstadoVazio icone="receipt-outline" titulo="Nenhuma venda no período" texto="As vendas lançadas no portal aparecem aqui." />
            )}
          </View>
        </>
      ) : null}
    </TelaModulo>
  );
}

function Atalhos({ pode, tabela }: { pode: { clientes: boolean; lancar: boolean; orcamentos: boolean }; tabela: string | null }) {
  const itens: { chave: string; rotulo: string; detalhe: string; icone: NomeIcone; cor: string; abrir: () => void }[] = [
    {
      chave: 'precos',
      rotulo: 'Tabela de preços',
      detalhe: tabela ? `Atualizada ${tempoRelativo(tabela)}` : 'Aparelhos e acessórios',
      icone: 'pricetags-outline',
      cor: marca[500],
      abrir: () => router.push('/modulo/vendas/precos' as Href),
    },
  ];
  if (pode.orcamentos)
    itens.push({
      chave: 'orcamento',
      rotulo: 'Orçamento',
      detalhe: 'Pelo seu WhatsApp',
      icone: 'logo-whatsapp',
      cor: VERDE_WHATSAPP,
      abrir: () => router.push('/modulo/vendas/orcamento' as Href),
    });
  if (pode.clientes)
    itens.push({
      chave: 'clientes',
      rotulo: 'Clientes',
      detalhe: 'Consulta e histórico',
      icone: 'people-outline',
      cor: estado.info,
      abrir: () => router.push('/modulo/vendas/clientes' as Href),
    });
  if (pode.lancar)
    itens.push({
      chave: 'lancar',
      rotulo: 'Lançar venda',
      detalhe: 'Abre no portal',
      icone: 'add-circle-outline',
      cor: COR_VENDAS,
      abrir: () => void abrirNoPortal('/vendas/nova/'),
    });
  return (
    <View className="flex-row flex-wrap gap-3">
      {itens.map((a, i) => (
        <Entrada key={a.chave} indice={i} style={{ flexBasis: '47%', flexGrow: 1 }}>
          <Cartao onPress={a.abrir} acessibilidade={a.rotulo} style={{ padding: 14, gap: 10, minHeight: 112 }}>
            <Bolinha icone={a.icone} cor={a.cor} />
            <View className="gap-0.5">
              <Texto variante="corpoForte">{a.rotulo}</Texto>
              <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                {a.detalhe}
              </Texto>
            </View>
          </Cartao>
        </Entrada>
      ))}
    </View>
  );
}

function Ranking({ titulo, icone, linhas }: { titulo: string; icone: NomeIcone; linhas: { nome: string; n: number }[] }) {
  const cores = useCores();
  const maior = Math.max(1, ...linhas.map((l) => l.n));
  return (
    <Secao titulo={titulo} icone={icone}>
      <View className="gap-3 py-2">
        {linhas.map((l) => (
          <View key={l.nome} className="gap-1">
            <View className="flex-row justify-between gap-2">
              <Texto variante="legenda" className="flex-1" numberOfLines={1}>
                {l.nome}
              </Texto>
              <Texto variante="legenda" tom="suave">
                {l.n}
              </Texto>
            </View>
            <View style={{ height: 6, borderRadius: 3, backgroundColor: cores.superficie3 }}>
              <View style={{ height: 6, borderRadius: 3, width: `${(l.n / maior) * 100}%`, backgroundColor: COR_VENDAS }} />
            </View>
          </View>
        ))}
      </View>
    </Secao>
  );
}
