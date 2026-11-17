import { type Href, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, Share, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { EsqueletoBloco, LinhaValor } from '@/components/modulos/comum';
import { Bolinha, BotaoWhatsApp, iconeDaCategoria, Secao } from '@/components/modulos/vendas/pecas';
import { BarraDetalhe, BotaoIcone, Botao, Cartao, Chip, EstadoErro, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { usePodeVendas, useProduto } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { Produto } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';
import { comAlfa, tempoRelativo } from '@/utils/formatar';

const CATEGORIA: Record<Produto['categoria_slv'], { rotulo: string; pilar: string }> = {
  aparelho: { rotulo: 'Aparelho', pilar: 'SMARTPHONES' },
  eletronico: { rotulo: 'Eletrônico', pilar: 'ELETRONICOS' },
  essencial: { rotulo: 'Essencial', pilar: 'ESSENCIAIS' },
};

/** O produto da tabela: preço por grupo de plano e condição, SKU por cor, e orçamento a partir dele. */
export default function DetalheProduto() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, isPending, isError, error, refetch } = useProduto(Number(id));

  return (
    <Tela>
      <BarraDetalhe
        titulo="Tabela de preços"
        direita={data ? <BotaoIcone icone="share-outline" rotulo="Compartilhar preço" onPress={() => void Share.share({ message: textoDoProduto(data, null, null) })} /> : undefined}
      />
      {isPending ? (
        <View className="gap-4 px-5 pt-2">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={5} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <Conteudo produto={data} />
      )}
    </Tela>
  );
}

function Conteudo({ produto }: { produto: Produto }) {
  const cores = useCores();
  const pode = usePodeVendas();
  const [grupo, setGrupo] = useState(0);
  const [sku, setSku] = useState<string | null>(produto.skus.length === 1 ? produto.skus[0].sku : null);
  const linha = produto.linhas[grupo] ?? null;
  const cor = produto.skus.find((s) => s.sku === sku) ?? null;
  const categoria = CATEGORIA[produto.categoria_slv] ?? CATEGORIA.eletronico;

  function orcar() {
    router.push({
      pathname: '/modulo/vendas/orcamento',
      params: { pilar: categoria.pilar, texto: textoDoProduto(produto, linha ? grupo : null, cor) },
    } as unknown as Href);
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 18, paddingBottom: 40 }}>
      <Animated.View entering={FadeInDown.duration(350)}>
        <Cartao style={{ padding: 18, gap: 12 }}>
          <View className="flex-row items-center gap-3">
            <Bolinha icone={iconeDaCategoria(produto.categoria)} cor={marca[500]} tamanho={48} />
            <View className="flex-1 flex-row flex-wrap gap-2">
              <Selo texto={categoria.rotulo} cor={marca[500]} ponto={false} />
              {produto.exige_serial ? <Selo texto="Pede serial" cor={estado.alerta} ponto={false} /> : null}
            </View>
          </View>
          <Texto variante="titulo2" selectable>
            {produto.nome}
          </Texto>
          {produto.marca || produto.tipo ? (
            <Texto variante="legenda" tom="suave">
              {[produto.marca, produto.tipo, produto.portfolio].filter(Boolean).join(' · ')}
            </Texto>
          ) : null}
          <View className="flex-row items-end gap-4">
            <View>
              <Texto variante="legenda" tom="fraco">
                a partir de
              </Texto>
              <Texto variante="display" style={{ fontFamily: fontes.extranegrito }}>
                {reais(produto.a_partir)}
              </Texto>
            </View>
            {produto.pix != null ? (
              <View style={{ paddingBottom: 6 }}>
                <Texto variante="legenda" tom="fraco">
                  no Pix
                </Texto>
                <Texto variante="titulo3" tom="sucesso">
                  {reais(produto.pix)}
                </Texto>
              </View>
            ) : null}
          </View>
          {produto.atualizado_em ? (
            <Texto variante="legenda" tom="fraco">
              Tabela atualizada {tempoRelativo(produto.atualizado_em)}
            </Texto>
          ) : null}
        </Cartao>
      </Animated.View>

      {produto.linhas.length ? (
        <View className="gap-3">
          <Texto variante="titulo3">Preço por plano</Texto>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
            {produto.linhas.map((l, i) => (
              <Chip key={l.grupo} rotulo={l.grupo} ativo={grupo === i} onPress={() => setGrupo(i)} />
            ))}
          </ScrollView>
          {linha ? (
            <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
              {produto.colunas.map((c, i) => {
                const celula = linha.celulas[i];
                if (!celula || celula.valor == null) return null;
                return (
                  <LinhaValor key={c.chave} rotulo={c.rotulo} valor={reais(celula.valor)} destaque={celula.menor} cor={celula.menor ? estado.sucesso : undefined} />
                );
              })}
            </Cartao>
          ) : null}
          <Texto variante="legenda" tom="fraco">
            Em verde, o menor valor da condição entre os planos.
          </Texto>
        </View>
      ) : (
        <Secao titulo="Preço" icone="pricetag-outline">
          <LinhaValor rotulo="Valor da tabela" valor={reais(produto.valor)} destaque />
          {produto.plano ? <LinhaValor rotulo="Plano" valor={produto.plano} /> : null}
        </Secao>
      )}

      <View className="gap-3">
        <Texto variante="titulo3">{produto.skus.length > 1 ? 'Cores e SKU' : 'Código'}</Texto>
        {produto.skus.length ? (
          <View className="gap-2">
            {produto.skus.map((s) => {
              const ativo = s.sku === sku;
              return (
                <Pressionavel
                  key={s.sku}
                  onPress={() => setSku(ativo ? null : s.sku)}
                  vibrar
                  accessibilityState={{ selected: ativo }}
                  accessibilityLabel={`Cor ${s.cor || 'sem cor'}, SKU ${s.sku}`}
                  style={{
                    flexDirection: 'row',
                    alignItems: 'center',
                    gap: 12,
                    padding: 14,
                    borderRadius: 14,
                    borderWidth: 1.5,
                    borderColor: ativo ? marca[500] : cores.borda,
                    backgroundColor: ativo ? comAlfa(marca[500], 0.08) : cores.superficie,
                  }}>
                  <View className="flex-1">
                    <Texto variante="corpoForte">{s.cor || 'Cor não informada'}</Texto>
                    <Texto variante="legenda" tom="suave" selectable>
                      SKU {s.sku}
                    </Texto>
                  </View>
                  {ativo ? <Selo texto="No orçamento" cor={marca[500]} /> : null}
                </Pressionavel>
              );
            })}
            {produto.skus.length > 1 ? (
              <Texto variante="legenda" tom="fraco">
                Toque na cor para levar o SKU certo para o orçamento.
              </Texto>
            ) : null}
          </View>
        ) : (
          <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
            <LinhaValor rotulo="SKU / código" valor={produto.sku ?? produto.cod_sap ?? '—'} />
          </Cartao>
        )}
      </View>

      <View className="gap-3">
        {pode?.orcamentos ? <BotaoWhatsApp titulo="Fazer orçamento deste produto" aoTocar={orcar} /> : null}
        <Botao titulo="Compartilhar preço" variante="secundario" icone="share-outline" onPress={() => void Share.share({ message: textoDoProduto(produto, linha ? grupo : null, cor) })} />
      </View>
    </ScrollView>
  );
}

/** O texto do produto para o orçamento ou para compartilhar: nome, cor/SKU e as condições do plano escolhido. */
function textoDoProduto(p: Produto, grupo: number | null, cor: { sku: string; cor: string } | null) {
  const linhas = [`📱 *${p.nome}*`];
  if (cor) linhas.push(`Cor: ${cor.cor || '—'} (SKU ${cor.sku})`);
  const linha = grupo != null ? p.linhas[grupo] : null;
  if (linha) {
    linhas.push(`Plano ${linha.grupo}:`);
    p.colunas.forEach((c, i) => {
      const v = linha.celulas[i]?.valor;
      if (v != null) linhas.push(`• ${c.rotulo}: ${reais(v)}`);
    });
  } else {
    linhas.push(`A partir de ${reais(p.a_partir)}${p.pix != null ? ` (Pix ${reais(p.pix)})` : ''}`);
  }
  return linhas.join('\n');
}
