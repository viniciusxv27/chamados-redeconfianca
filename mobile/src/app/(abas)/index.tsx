import { router } from 'expo-router';
import { RefreshControl, ScrollView, View } from 'react-native';
import Animated, { FadeInDown, useAnimatedScrollHandler, useAnimatedStyle, useSharedValue, interpolate, Extrapolation } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CartaoChamado } from '@/components/chamados/cartao-chamado';
import { CartaoComunicado } from '@/components/comunicados/cartao-comunicado';
import { Avatar, Cartao, Entrada, Esqueleto, EsqueletoCartao, EstadoErro, Icone, type NomeIcone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, marca, raios } from '@/constants/theme';
import { useInicio } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { useSessao } from '@/providers/sessao';
import { useCores } from '@/providers/tema';
import { comAlfa, saudacao } from '@/utils/formatar';

export default function Inicio() {
  const { usuario } = useSessao();
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const { data, isPending, isError, error, refetch, isRefetching } = useInicio();
  const pessoa = data?.usuario ?? usuario;

  // O cabeçalho encolhe e ganha fundo conforme a pessoa rola.
  const rolagem = useSharedValue(0);
  const aoRolar = useAnimatedScrollHandler((e) => {
    rolagem.set(e.contentOffset.y);
  });
  const barra = useAnimatedStyle(() => ({ opacity: interpolate(rolagem.get(), [40, 90], [0, 1], Extrapolation.CLAMP) }));

  return (
    <Tela>
      <Animated.View
        pointerEvents="none"
        style={[{ position: 'absolute', top: 0, left: 0, right: 0, height: insets.top + 8, backgroundColor: cores.fundo, zIndex: 10, borderBottomWidth: 1, borderBottomColor: cores.borda }, barra]}
      />
      <Animated.ScrollView
        onScroll={aoRolar}
        scrollEventThrottle={16}
        contentContainerStyle={{ paddingTop: insets.top + 12, paddingBottom: 110 }}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}>
        {/* Saudação */}
        <Animated.View entering={FadeInDown.duration(400)} className="flex-row items-center gap-3 px-5 pb-5">
          <View className="flex-1">
            <Texto variante="legenda" tom="fraco">
              {saudacao()},
            </Texto>
            <Texto variante="titulo1" numberOfLines={1}>
              {pessoa?.primeiro_nome ?? ''} 👋
            </Texto>
            {pessoa?.setor ? (
              <Texto variante="legenda" tom="suave" numberOfLines={1}>
                {pessoa.cargo ? `${pessoa.cargo} · ` : ''}
                {pessoa.setor}
              </Texto>
            ) : null}
          </View>
          <Pressionavel onPress={() => router.navigate('/perfil')} escala={0.92} accessibilityLabel="Abrir perfil">
            <Avatar nome={pessoa?.nome ?? '?'} foto={pessoa?.foto} tamanho={52} borda={marca[500]} />
          </Pressionavel>
        </Animated.View>

        {isError && !data ? (
          <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
        ) : (
          <View className="gap-7">
            {/* Números dos chamados */}
            <View className="px-5">
              {isPending ? (
                <View className="flex-row flex-wrap gap-3">
                  {[0, 1, 2, 3].map((i) => (
                    <Cartao key={i} style={{ flexBasis: '47%', flexGrow: 1, padding: 16, gap: 10 }}>
                      <Esqueleto largura={32} altura={32} raio={10} />
                      <Esqueleto largura={48} altura={24} />
                      <Esqueleto largura="80%" altura={11} />
                    </Cartao>
                  ))}
                </View>
              ) : data ? (
                <View className="flex-row flex-wrap gap-3">
                  <Numero indice={0} icone="person-outline" cor={marca[500]} valor={data.chamados.comigo} rotulo="Comigo para resolver" aoTocar={() => router.navigate({ pathname: '/chamados', params: { escopo: 'comigo' } })} />
                  <Numero indice={1} icone="paper-plane-outline" cor={estado.info} valor={data.chamados.abertos_por_mim} rotulo="Abertos por mim" aoTocar={() => router.navigate({ pathname: '/chamados', params: { escopo: 'meus' } })} />
                  <Numero indice={2} icone="checkmark-done-outline" cor={estado.alerta} valor={data.chamados.aguardando_minha_aprovacao} rotulo="Aguardando sua aprovação" aoTocar={() => router.navigate({ pathname: '/chamados', params: { escopo: 'meus' } })} />
                  <Numero indice={3} icone="alarm-outline" cor={estado.perigo} valor={data.chamados.atrasados} rotulo="Atrasados que você vê" aoTocar={() => router.navigate('/chamados')} />
                </View>
              ) : null}
            </View>

            {/* Comunicados obrigatórios pendentes */}
            {data?.comunicados_obrigatorios.length ? (
              <Entrada indice={2} style={{ paddingHorizontal: 20 }}>
                <Cartao
                  onPress={() => router.push({ pathname: '/comunicado/[id]', params: { id: String(data.comunicados_obrigatorios[0].id) } })}
                  style={{ padding: 16, flexDirection: 'row', alignItems: 'center', gap: 12, borderColor: comAlfa(estado.perigo, 0.35), backgroundColor: comAlfa(estado.perigo, 0.06) }}>
                  <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(estado.perigo, 0.14), alignItems: 'center', justifyContent: 'center' }}>
                    <Icone nome="megaphone" tamanho={20} cor={estado.perigo} />
                  </View>
                  <View className="flex-1">
                    <Texto variante="corpoForte">
                      {data.comunicados_obrigatorios.length === 1 ? '1 comunicado obrigatório' : `${data.comunicados_obrigatorios.length} comunicados obrigatórios`}
                    </Texto>
                    <Texto variante="legenda" tom="suave" numberOfLines={1}>
                      {data.comunicados_obrigatorios[0].titulo}
                    </Texto>
                  </View>
                  <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
                </Cartao>
              </Entrada>
            ) : null}

            {/* Chamados recentes */}
            <View className="gap-3">
              <TituloSecao titulo="Seus chamados" acao="Ver todos" aoAgir={() => router.navigate('/chamados')} />
              <View className="gap-3 px-5">
                {isPending ? (
                  <>
                    <EsqueletoCartao />
                    <EsqueletoCartao />
                  </>
                ) : data?.chamados_recentes.length ? (
                  data.chamados_recentes.map((c, i) => (
                    <Entrada key={c.id} indice={i + 3}>
                      <CartaoChamado chamado={c} />
                    </Entrada>
                  ))
                ) : (
                  <Cartao onPress={() => router.push('/chamado/novo')} style={{ padding: 20, alignItems: 'center', gap: 8 }}>
                    <Icone nome="add-circle-outline" tamanho={28} cor={marca[500]} />
                    <Texto variante="corpoForte">Nenhum chamado por aqui</Texto>
                    <Texto variante="legenda" tom="suave">
                      Toque para abrir o primeiro.
                    </Texto>
                  </Cartao>
                )}
              </View>
            </View>

            {/* Mural */}
            {data && data.comunicados_fixados.length + data.comunicados.length > 0 ? (
              <View className="gap-3">
                <TituloSecao titulo="Mural" acao="Ver mural" aoAgir={() => router.navigate('/comunicados')} />
                <ScrollView horizontal showsHorizontalScrollIndicator={false} snapToInterval={292} decelerationRate="fast" contentContainerStyle={{ paddingHorizontal: 20, gap: 12 }}>
                  {[...data.comunicados_fixados, ...data.comunicados].map((c, i) => (
                    <Entrada key={c.id} indice={i + 4} style={{ width: 280 }}>
                      <CartaoComunicado comunicado={c} compacto />
                    </Entrada>
                  ))}
                </ScrollView>
              </View>
            ) : null}
          </View>
        )}
      </Animated.ScrollView>

      {/* Ação principal sempre à mão */}
      <Animated.View entering={FadeInDown.delay(500).springify()} style={{ position: 'absolute', right: 20, bottom: 20 }}>
        <Pressionavel
          onPress={() => router.push('/chamado/novo')}
          vibrar
          escala={0.92}
          accessibilityLabel="Abrir novo chamado"
          style={{ height: 56, paddingHorizontal: 20, borderRadius: raios.pilula, backgroundColor: marca[500], flexDirection: 'row', alignItems: 'center', gap: 8, boxShadow: '0px 10px 24px rgba(255,107,53,0.45)' }}>
          <Icone nome="add" tamanho={24} cor="#fff" />
          <Texto variante="corpoForte" tom="branco">
            Novo chamado
          </Texto>
        </Pressionavel>
      </Animated.View>
    </Tela>
  );
}

function TituloSecao({ titulo, acao, aoAgir }: { titulo: string; acao?: string; aoAgir?: () => void }) {
  return (
    <View className="flex-row items-center justify-between px-5">
      <Texto variante="titulo3">{titulo}</Texto>
      {acao && aoAgir ? (
        <Pressionavel onPress={aoAgir} hitSlop={8} escala={0.94}>
          <Texto variante="legenda" tom="marca" style={{ fontSize: 14 }}>
            {acao}
          </Texto>
        </Pressionavel>
      ) : null}
    </View>
  );
}

function Numero({ indice, icone, cor, valor, rotulo, aoTocar }: { indice: number; icone: NomeIcone; cor: string; valor: number; rotulo: string; aoTocar: () => void }) {
  return (
    <Entrada indice={indice} style={{ flexBasis: '47%', flexGrow: 1 }}>
      <Cartao onPress={aoTocar} style={{ padding: 16, gap: 10 }} acessibilidade={`${valor} ${rotulo}`}>
        <View style={{ width: 36, height: 36, borderRadius: 11, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={icone} tamanho={19} cor={cor} />
        </View>
        <Texto variante="titulo1" style={valor > 0 && cor === estado.perigo ? { color: cor } : undefined}>
          {valor}
        </Texto>
        <Texto variante="legenda" tom="suave" numberOfLines={2}>
          {rotulo}
        </Texto>
      </Cartao>
    </Entrada>
  );
}
