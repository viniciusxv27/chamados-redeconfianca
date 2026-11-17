import { useMemo, useState } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { BarraProgresso } from '@/components/modulos/impulso/barra';
import { pontos } from '@/components/modulos/impulso/comum';
import { Avatar, BarraDetalhe, BotaoIcone, CabecalhoGrande, Campo, Cartao, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, Tela, Texto } from '@/components/ui';
import { fontes, marca } from '@/constants/theme';
import { useRanking } from '@/hooks/modulos/impulso';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { LinhaRanking } from '@/services/modulos/impulso';
import { comAlfa, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const MEDALHA = ['#D4AF37', '#94A3B8', '#B45309'];

function semAcento(t: string) {
  return t
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

/** Ranking ao vivo do mês (o de /impulso/acompanhamento/). A busca filtra a lista sem mudar a posição. */
export default function RankingImpulso() {
  const { data, isPending, isError, error, refetch, isRefetching } = useRanking();
  const [busca, setBusca] = useState('');
  const linhas = useMemo(() => {
    const termo = semAcento(busca.trim());
    const todas = data?.linhas ?? [];
    return termo ? todas.filter((l) => semAcento(l.nome).includes(termo)) : todas;
  }, [data, busca]);

  const cabecalho = (
    <View className="gap-3 pb-3">
      <CabecalhoGrande titulo="Ranking" subtitulo={data ? `Mês de ${data.periodo.rotulo}` : 'Impulso'} />
      <View className="gap-3 px-5">
        {data?.minha_posicao ? (
          <Cartao style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12, borderColor: comAlfa(marca[500], 0.4) }}>
            <Icone nome="trophy-outline" tamanho={20} cor={marca[500]} />
            <Texto variante="corpoForte" className="flex-1">{`Você está em ${data.minha_posicao}º de ${data.total}`}</Texto>
          </Cartao>
        ) : null}
        <Campo icone="search" placeholder="Buscar pessoa" value={busca} onChangeText={setBusca} autoCorrect={false} clearButtonMode="while-editing" />
        {data ? (
          <Texto variante="legenda" tom="fraco">
            {`Pontuação ao vivo, congelada quando o mês é finalizado. Atualizada ${tempoRelativo(data.calculado_em)}.`}
          </Texto>
        ) : null}
      </View>
    </View>
  );

  return (
    <Tela>
      <BarraDetalhe direita={<BotaoIcone icone="open-outline" rotulo="Abrir o acompanhamento no portal" onPress={() => abrirNoPortal('/impulso/acompanhamento/')} />} />
      {isPending ? (
        <View>
          {cabecalho}
          <View className="gap-3 px-5">
            <Texto variante="legenda" tom="fraco">
              Calculando a pontuação de todo mundo…
            </Texto>
            <EsqueletoLista quantos={5} />
          </View>
        </View>
      ) : isError && !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <FlatList<LinhaRanking>
          data={linhas}
          keyExtractor={(l) => String(l.id)}
          ListHeaderComponent={cabecalho}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 10 ? index : 0} style={{ paddingHorizontal: 20 }}>
              <LinhaDoRanking linha={item} />
            </Entrada>
          )}
          contentContainerStyle={{ paddingBottom: 48, gap: 10 }}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          keyboardDismissMode="on-drag"
          ListEmptyComponent={<EstadoVazio icone="podium-outline" titulo={busca ? 'Ninguém com esse nome' : 'Ranking vazio'} texto={busca ? undefined : 'Ninguém do Escritório no ranking ainda.'} />}
        />
      )}
    </Tela>
  );
}

function LinhaDoRanking({ linha }: { linha: LinhaRanking }) {
  const cores = useCores();
  const medalha = linha.posicao <= 3 ? MEDALHA[linha.posicao - 1] : null;
  return (
    <Cartao style={{ padding: 14, gap: 10, borderColor: linha.sou_eu ? marca[500] : undefined, backgroundColor: linha.sou_eu ? comAlfa(marca[500], 0.06) : undefined }}>
      <View className="flex-row items-center gap-3">
        <View style={{ width: 30, alignItems: 'center' }}>
          {medalha ? (
            <Icone nome="medal" tamanho={22} cor={medalha} />
          ) : (
            <Texto variante="corpoForte" tom="fraco">
              {`${linha.posicao}º`}
            </Texto>
          )}
        </View>
        <Avatar nome={linha.nome} foto={linha.foto} tamanho={36} />
        <View className="flex-1 gap-0.5">
          <Texto variante="corpoForte" numberOfLines={1}>
            {linha.sou_eu ? `${linha.nome} (você)` : linha.nome}
          </Texto>
          <Texto variante="legenda" style={{ color: linha.faixa.cor, fontFamily: fontes.seminegrito }}>
            {`${linha.faixa.rotulo}${linha.empatou ? ' · empate decidido no desempate' : ''}`}
          </Texto>
        </View>
        <Texto variante="titulo3" style={{ color: cores.texto }}>{`${Math.round(linha.percentual)}%`}</Texto>
      </View>
      <BarraProgresso pct={linha.percentual} cor={linha.faixa.cor} altura={6} />
      <View className="flex-row justify-between">
        <Texto variante="legenda" tom="fraco">{`Confiar ${pontos(linha.confiar)}`}</Texto>
        <Texto variante="legenda" tom="fraco">{`Conectar ${pontos(linha.conectar)}`}</Texto>
        <Texto variante="legenda" tom="fraco">{`Inovar ${pontos(linha.inovar)}`}</Texto>
      </View>
    </Cartao>
  );
}
