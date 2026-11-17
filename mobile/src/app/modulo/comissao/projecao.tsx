import { useState } from 'react';
import { View } from 'react-native';

import { Indicador, TelaModulo } from '@/components/modulos/comum';
import { Cartao, Chip, Entrada, EstadoVazio, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useProjecaoComissao } from '@/hooks/modulos/comissao';
import { reais } from '@/utils/dinheiro';
import { dataHora } from '@/utils/formatar';

export default function ProjecaoComissao() {
  const { data, isPending, error, refetch, isRefetching } = useProjecaoComissao();
  const [papel, setPapel] = useState<string | null>(null);
  const linhas = (data?.linhas ?? []).filter((l) => !papel || l.papel === papel);
  const comDado = linhas.filter((l) => l.tem_dado);
  const media = comDado.length ? comDado.reduce((s, l) => s + l.ganho, 0) / comDado.length : 0;

  return (
    <TelaModulo
      titulo="Médias de comissão"
      subtitulo={data?.referencia ? `Projeção · ${data.referencia}` : 'Projeção'}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}>
      {data?.status === 'calculando' ? (
        <EstadoVazio icone="hourglass-outline" titulo="Calculando as médias" texto="A projeção da rede está sendo calculada. Volte em alguns minutos." />
      ) : data ? (
        <>
          {data.gerado_em ? (
            <Texto variante="legenda" tom="fraco">
              Calculado em {dataHora(data.gerado_em)}
              {data.desatualizado ? ' · atualizando' : ''}
            </Texto>
          ) : null}
          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo={papel ? 'Média do filtro' : 'Média'} valor={reais(papel ? media : data.resumo.media)} icone="analytics-outline" cor={marca[500]} />
            <Indicador rotulo="Maior" valor={reais(papel ? Math.max(0, ...linhas.map((l) => l.ganho)) : data.resumo.maior)} icone="trending-up-outline" cor={estado.sucesso} />
            {data.referencia_loja ? (
              <Indicador rotulo="Média dos consultores da loja" valor={reais(data.referencia_loja.media)} icone="storefront-outline" cor={estado.info} />
            ) : (
              <Indicador rotulo="Pessoas" valor={String(linhas.length)} icone="people-outline" />
            )}
          </View>

          {data.papeis.length > 1 ? (
            <View className="flex-row flex-wrap gap-2">
              <Chip rotulo="Todos" ativo={!papel} onPress={() => setPapel(null)} />
              {data.papeis.map((p) => (
                <Chip key={p.chave} rotulo={`${p.rotulo} (${p.quantos})`} ativo={papel === p.chave} onPress={() => setPapel(p.chave)} />
              ))}
            </View>
          ) : null}

          {linhas.length === 0 ? (
            <EstadoVazio icone="stats-chart-outline" titulo="Ninguém por aqui" texto="Não há projeção para o seu recorte." />
          ) : (
            <Cartao style={{ paddingHorizontal: 16, paddingVertical: 6 }}>
              {linhas.map((l, i) => (
                <Entrada key={l.id} indice={i}>
                  <View className="flex-row items-center gap-3 py-2">
                    <View className="flex-1">
                      <Texto variante="corpoForte" numberOfLines={1}>
                        {l.nome}
                      </Texto>
                      <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                        {[l.papel_rotulo, l.setor].filter(Boolean).join(' · ')}
                      </Texto>
                    </View>
                    <Texto variante="corpoForte" tom={l.tem_dado ? 'padrao' : 'fraco'}>
                      {l.tem_dado ? reais(l.ganho) : 'sem dado'}
                    </Texto>
                  </View>
                </Entrada>
              ))}
            </Cartao>
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}
