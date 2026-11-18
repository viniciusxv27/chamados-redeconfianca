/**
 * Power BI — os dashboards do /power-bi/ liberados para a pessoa (mesmo recorte
 * da web: setor, grupo, hierarquia e liberação individual), com busca e filtro
 * por origem. O app não tem WebView: o painel abre no navegador do sistema
 * (direto, se o cadastro permite nova guia; senão pela página do portal).
 */
import { Image } from 'expo-image';
import { useMemo, useState } from 'react';
import { ActivityIndicator, Alert, ScrollView, View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Campo, Cartao, Chip, Entrada, EstadoVazio, Icone, type NomeIcone, Texto } from '@/components/ui';
import { marca, raios } from '@/constants/theme';
import { useAbrirDashboard, useDashboards } from '@/hooks/modulos/power_bi';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { Dashboard } from '@/services/modulos/power_bi';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

type Origem = 'todos' | 'power_bi' | 'outros';

const ORIGENS: { valor: Origem; rotulo: string }[] = [
  { valor: 'todos', rotulo: 'Todos' },
  { valor: 'power_bi', rotulo: 'Power BI' },
  { valor: 'outros', rotulo: 'Outros painéis' },
];

/** Ícone Font Awesome do cadastro → Ionicons. */
const ICONES: [RegExp, NomeIcone][] = [
  [/signal/, 'cellular-outline'],
  [/chart-bar|chart-column/, 'bar-chart-outline'],
  [/chart-pie/, 'pie-chart-outline'],
  [/chart-line|chart-area/, 'trending-up-outline'],
  [/users|user/, 'people-outline'],
  [/coins|money|dollar|wallet/, 'cash-outline'],
  [/map|route|truck/, 'map-outline'],
  [/tv|desktop/, 'tv-outline'],
  [/box|warehouse|cubes/, 'cube-outline'],
  [/tag/, 'pricetag-outline'],
];

function iconeDe(classe: string): NomeIcone {
  return ICONES.find(([re]) => re.test(classe))?.[1] ?? 'analytics-outline';
}

function corDe(cor: string) {
  return /^#[0-9a-f]{6}$/i.test(cor) ? cor : marca[500];
}

function semAcento(s: string) {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

export default function PowerBi() {
  const { data, isPending, error, refetch, isRefetching } = useDashboards();
  const abrir = useAbrirDashboard();
  const [busca, setBusca] = useState('');
  const [origem, setOrigem] = useState<Origem>('todos');
  const [abrindo, setAbrindo] = useState<number | null>(null);

  const lista = useMemo(() => data ?? [], [data]);
  const temAsDuas = lista.some((d) => d.origem === 'power_bi') && lista.some((d) => d.origem === 'outros');
  const filtrados = useMemo(() => {
    const termo = semAcento(busca.trim());
    return lista.filter(
      (d) => (origem === 'todos' || d.origem === origem) && (!termo || semAcento(`${d.nome} ${d.descricao}`).includes(termo)),
    );
  }, [lista, busca, origem]);

  function aoAbrir(d: Dashboard) {
    if (abrindo) return;
    setAbrindo(d.id);
    abrir.mutate(d.id, {
      onSuccess: (r) => void abrirNoPortal(r.url),
      onError: (e) => Alert.alert('Não deu para abrir', mensagemDoErro(e)),
      onSettled: () => setAbrindo(null),
    });
  }

  const subtitulo = data ? `${lista.length} ${lista.length === 1 ? 'dashboard liberado' : 'dashboards liberados'}` : 'Dashboards do seu perfil';

  return (
    <TelaModulo
      titulo="Power BI"
      subtitulo={subtitulo}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/power-bi/')} />}>
      {lista.length === 0 ? (
        <EstadoVazio icone="analytics-outline" titulo="Nenhum dashboard liberado" texto="Peça ao SUPERADMIN a liberação do acesso." />
      ) : (
        <>
          <Campo icone="search" placeholder="Buscar dashboard" value={busca} onChangeText={setBusca} returnKeyType="search" autoCorrect={false} clearButtonMode="while-editing" />
          {temAsDuas ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }} style={{ marginHorizontal: -20 }}>
              <View style={{ width: 12 }} />
              {ORIGENS.map((o) => (
                <Chip key={o.valor} rotulo={o.rotulo} ativo={origem === o.valor} onPress={() => setOrigem(o.valor)} />
              ))}
              <View style={{ width: 12 }} />
            </ScrollView>
          ) : null}
          {filtrados.length === 0 ? (
            <EstadoVazio icone="search-outline" titulo="Nada encontrado" texto={busca ? `Nenhum dashboard com "${busca.trim()}".` : 'Troque o filtro para ver mais.'} />
          ) : (
            filtrados.map((d, i) => (
              <Entrada key={d.id} indice={i < 10 ? i : 0}>
                <CartaoDashboard dashboard={d} abrindo={abrindo === d.id} onPress={() => aoAbrir(d)} />
              </Entrada>
            ))
          )}
          <Texto variante="legenda" tom="fraco" className="mt-1">
            Os painéis abrem no navegador. Alguns pedem o login do portal ou da Microsoft na primeira vez.
          </Texto>
        </>
      )}
    </TelaModulo>
  );
}

function CartaoDashboard({ dashboard: d, abrindo, onPress }: { dashboard: Dashboard; abrindo: boolean; onPress: () => void }) {
  const cores = useCores();
  const cor = corDe(d.cor);
  return (
    <Cartao onPress={onPress} acessibilidade={`Abrir ${d.nome}`} style={{ overflow: 'hidden' }}>
      {d.imagem ? (
        <View style={{ height: 96, backgroundColor: comAlfa(cor, 0.25) }}>
          <Image source={{ uri: d.imagem }} style={{ width: '100%', height: '100%' }} contentFit="cover" transition={200} />
          <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.25)' }} />
        </View>
      ) : (
        <View style={{ height: 6, backgroundColor: cor }} />
      )}
      <View style={{ padding: 18, gap: 12 }}>
        <View className="flex-row items-center" style={{ gap: 14 }}>
          <View style={{ width: 46, height: 46, borderRadius: raios.md, backgroundColor: comAlfa(cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={iconeDe(d.icone)} tamanho={22} cor={cor} />
          </View>
          <View className="flex-1" style={{ gap: 2 }}>
            <Texto variante="titulo3" numberOfLines={2}>
              {d.nome}
            </Texto>
            <Texto variante="legenda" tom="fraco">
              {d.origem === 'power_bi' ? 'Power BI' : 'Painel'}
            </Texto>
          </View>
        </View>
        {d.descricao ? (
          <Texto variante="corpo" tom="suave" numberOfLines={3}>
            {d.descricao}
          </Texto>
        ) : null}
        <View className="flex-row items-center justify-between" style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave, paddingTop: 12 }}>
          <Texto variante="legenda" tom="fraco">
            {d.abre_direto ? 'Abre direto no painel' : 'Abre pela página do portal'}
          </Texto>
          {abrindo ? (
            <ActivityIndicator color={marca[500]} />
          ) : (
            <View className="flex-row items-center" style={{ gap: 4 }}>
              <Texto variante="corpoForte" tom="marca">
                Abrir
              </Texto>
              <Icone nome="arrow-forward" tamanho={16} cor={marca[600]} />
            </View>
          )}
        </View>
      </View>
    </Cartao>
  );
}
