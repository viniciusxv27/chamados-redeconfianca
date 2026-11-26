/**
 * Início — a home do portal (templates/home.html) no celular, na mesma ordem:
 * topo em degradê (saudação pela hora, perfil, saldo C$ só para quem vê no portal e
 * os atalhos), comunicados obrigatórios (não aparece para o SUPERADMIN, como os popups
 * do portal), Comunicados Fixados, Reconhecimento (faixa com os 12 mais recentes e o
 * mural), Feed de Comunicados com "Carregar mais" — e, por baixo, os chamados da pessoa.
 * Desde 25/11/2026, como no portal: o cartão do Impulso logo abaixo do topo (só para quem é do
 * Impulso) e o Feed da rede (fotos e vídeos, os 3 mais recentes) depois do Reconhecimento.
 */
import { type Href, router } from 'expo-router';
import { FlatList, RefreshControl, View } from 'react-native';
import Animated, { Extrapolation, FadeInDown, interpolate, useAnimatedScrollHandler, useAnimatedStyle, useSharedValue } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CartaoChamado } from '@/components/chamados/cartao-chamado';
import { EsqueletoPost, PostComunicado } from '@/components/comunicados/post-comunicado';
import { CartaoReconhecimento } from '@/components/elogios/cartao-reconhecimento';
import { CartaoImpulso } from '@/components/inicio/cartao-impulso';
import { NumeroQueConta, TopoInicio } from '@/components/inicio/topo';
import { BotaoPublicar, EsqueletoPostSocial, PostSocial } from '@/components/social/post-social';
import {
  Botao,
  Cartao,
  Entrada,
  Esqueleto,
  EsqueletoCartao,
  EstadoErro,
  EstadoVazio,
  GrupoEsqueleto,
  Icone,
  type NomeIcone,
  Pressionavel,
  Tela,
  Texto,
} from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { chaves, useFeed, useFeedSocial, useInicio } from '@/hooks/consultas';
import { useQueryClient } from '@tanstack/react-query';
import { mensagemDoErro } from '@/services/api';
import { useSessao } from '@/providers/sessao';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const LARGURA_RECONHECIMENTO = 270;

export default function Inicio() {
  const { usuario } = useSessao();
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const inicio = useInicio();
  const feed = useFeed();
  const rede = useFeedSocial(3);
  const cliente = useQueryClient();
  const { data } = inicio;
  const pessoa = data?.usuario ?? usuario;
  const posts = feed.data?.pages.flatMap((p) => p.resultados) ?? [];

  // A faixa do topo ganha fundo quando a pessoa rola (o conteúdo não fica por baixo do relógio).
  const rolagem = useSharedValue(0);
  const aoRolar = useAnimatedScrollHandler((e) => {
    rolagem.set(e.contentOffset.y);
  });
  const barra = useAnimatedStyle(() => ({ opacity: interpolate(rolagem.get(), [30, 80], [0, 1], Extrapolation.CLAMP) }));

  const postsDaRede = rede.data?.pages[0]?.resultados ?? [];

  function atualizar() {
    void inicio.refetch();
    void feed.refetch();
    void rede.refetch();
    void cliente.invalidateQueries({ queryKey: chaves.impulsoHome });
  }

  return (
    <Tela>
      <Animated.View
        pointerEvents="none"
        style={[{ position: 'absolute', top: 0, left: 0, right: 0, height: insets.top, backgroundColor: cores.fundo, zIndex: 10, borderBottomWidth: 1, borderBottomColor: cores.borda }, barra]}
      />
      <Animated.ScrollView
        onScroll={aoRolar}
        scrollEventThrottle={16}
        contentContainerStyle={{ paddingTop: insets.top + 16, paddingBottom: 120, gap: 32 }}
        refreshControl={<RefreshControl refreshing={inicio.isRefetching || rede.isRefetching || (feed.isRefetching && !feed.isFetchingNextPage)} onRefresh={atualizar} tintColor={marca[500]} colors={[marca[500]]} />}>
        {/* Topo em degradê: saudação pela hora, perfil, saldo e atalhos (o estilo novo do portal) */}
        <View style={{ paddingHorizontal: 20 }}>
          <TopoInicio
            primeiroNome={pessoa?.primeiro_nome ?? ''}
            nome={pessoa?.nome ?? ''}
            foto={pessoa?.foto}
            linha={[pessoa?.hierarquia_nome, pessoa?.setor].filter(Boolean).join(' · ')}
            saldo={data?.saldo_cs ?? null}
            saudacao={data?.saudacao}
            podeCriarComunicado={!!data?.pode_criar_comunicado}
            avisosOcultos={data?.avisos_ocultos?.length ?? 0}
          />
        </View>

        {inicio.isError && !data ? (
          <EstadoErro mensagem={mensagemDoErro(inicio.error)} aoTentar={atualizar} />
        ) : (
          <>
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

            {/* Cartão do Impulso (só para quem é do Impulso; vem à parte, sem atrasar a home) */}
            <View style={{ paddingHorizontal: 20, marginBottom: -8 }}>
              <CartaoImpulso />
            </View>

            {/* Comunicados Fixados */}
            {inicio.isPending ? (
              <Secao icone="pin" cor={estado.perigo} titulo="Comunicados Fixados">
                <EsqueletoPost comImagem={false} />
              </Secao>
            ) : data?.comunicados_fixados.length ? (
              <Secao icone="pin" cor={estado.perigo} titulo="Comunicados Fixados">
                {data.comunicados_fixados.map((c, i) => (
                  <Entrada key={c.id} indice={i + 2}>
                    <PostComunicado comunicado={c} />
                  </Entrada>
                ))}
              </Secao>
            ) : null}

            {/* Reconhecimento (antes "Elogios Recentes"): faixa com os 12 mais recentes + o mural */}
            {inicio.isPending ? (
              <Secao icone="ribbon" cor="#F59E0B" titulo="Reconhecimento">
                <GrupoEsqueleto>
                  <View className="flex-row gap-3">
                    {[0, 1].map((i) => (
                      <Cartao key={i} style={{ width: LARGURA_RECONHECIMENTO, padding: 16, gap: 10 }}>
                        <View className="flex-row gap-3">
                          <Esqueleto largura={44} altura={44} raio={22} />
                          <View className="flex-1 gap-2">
                            <Esqueleto largura="80%" />
                            <Esqueleto largura={70} altura={11} />
                          </View>
                        </View>
                        <Esqueleto largura="95%" altura={11} />
                        <Esqueleto largura="70%" altura={11} />
                      </Cartao>
                    ))}
                  </View>
                </GrupoEsqueleto>
              </Secao>
            ) : data ? (
              <Secao
                icone="ribbon"
                cor="#F59E0B"
                titulo="Reconhecimento"
                subtitulo={
                  data.total_reconhecimentos ? (
                    <>
                      <NumeroQueConta valor={data.total_reconhecimentos} /> {data.total_reconhecimentos === 1 ? 'reconhecimento na rede' : 'reconhecimentos na rede'}
                    </>
                  ) : (
                    'Quem fez a diferença aparece aqui'
                  )
                }
                acao="Ver todos"
                aoAgir={() => router.push('/reconhecimento' as Href)}>
                {data.elogios.length ? (
                  <FlatList
                    data={data.elogios}
                    horizontal
                    keyExtractor={(e) => String(e.id)}
                    showsHorizontalScrollIndicator={false}
                    snapToInterval={LARGURA_RECONHECIMENTO + 12}
                    decelerationRate="fast"
                    style={{ marginHorizontal: -20 }}
                    contentContainerStyle={{ paddingHorizontal: 20, gap: 12 }}
                    renderItem={({ item, index }) => (
                      <Entrada indice={index < 4 ? index + 3 : 0} style={{ width: LARGURA_RECONHECIMENTO }}>
                        <CartaoReconhecimento elogio={item} largura={LARGURA_RECONHECIMENTO} />
                      </Entrada>
                    )}
                    ListFooterComponent={
                      <Pressionavel
                        onPress={() => router.push('/reconhecimento' as Href)}
                        escala={0.96}
                        accessibilityLabel="Ver todos os reconhecimentos"
                        style={{ width: 150, alignSelf: 'stretch', minHeight: 180, borderRadius: raios['2xl'], borderWidth: 2, borderStyle: 'dashed', borderColor: comAlfa(marca[500], 0.35), alignItems: 'center', justifyContent: 'center', gap: 8, padding: 14 }}>
                        <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: comAlfa(marca[500], 0.12), alignItems: 'center', justifyContent: 'center' }}>
                          <Icone nome="arrow-forward" tamanho={20} cor={marca[500]} />
                        </View>
                        <Texto variante="legenda" tom="marca" className="text-center" style={{ fontFamily: fontes.seminegrito }}>
                          Ver todos os reconhecimentos
                        </Texto>
                        {data.total_reconhecimentos ? (
                          <Texto variante="legenda" tom="fraco" style={{ fontSize: 12 }}>
                            {data.total_reconhecimentos} no mural
                          </Texto>
                        ) : null}
                      </Pressionavel>
                    }
                  />
                ) : (
                  <Cartao onPress={() => abrirNoPortal('/compliments/create/')} style={{ padding: 20, alignItems: 'center', gap: 8 }}>
                    <Icone nome="ribbon-outline" tamanho={28} cor="#F59E0B" />
                    <Texto variante="corpoForte">Ninguém foi reconhecido ainda</Texto>
                    <Texto variante="legenda" tom="suave" className="text-center">
                      Seja o primeiro a reconhecer o trabalho de um colega.
                    </Texto>
                  </Cartao>
                )}
              </Secao>
            ) : null}

            {/* Feed da rede: fotos e vídeos com curtidas e comentários (os 3 mais recentes) */}
            <Secao icone="camera" cor="#EC4899" titulo="Feed da rede" subtitulo="Fotos e vídeos da rede" acao="Abrir o feed" aoAgir={() => router.push('/feed' as Href)}>
              <BotaoPublicar />
              {rede.isPending ? (
                <EsqueletoPostSocial />
              ) : rede.isError && !postsDaRede.length ? (
                <EstadoErro mensagem={mensagemDoErro(rede.error)} aoTentar={() => void rede.refetch()} />
              ) : postsDaRede.length ? (
                <>
                  {postsDaRede.map((p, i) => (
                    <Entrada key={p.id} indice={i + 3}>
                      <PostSocial post={p} />
                    </Entrada>
                  ))}
                  {rede.data?.pages[0]?.tem_mais ? (
                    <Botao titulo="Ver mais publicações" variante="secundario" icone="images-outline" onPress={() => router.push('/feed' as Href)} />
                  ) : null}
                </>
              ) : (
                <EstadoVazio icone="camera-outline" titulo="Ninguém postou ainda" texto="Que tal ser o primeiro a compartilhar um momento?" />
              )}
            </Secao>

            {/* Feed de Comunicados */}
            <Secao icone="radio-outline" cor={marca[500]} titulo="Feed de Comunicados" acao="Mural" aoAgir={() => router.push('/comunicados')}>
              {feed.isPending ? (
                <>
                  <EsqueletoPost />
                  <EsqueletoPost comImagem={false} />
                </>
              ) : feed.isError && !posts.length ? (
                <EstadoErro mensagem={mensagemDoErro(feed.error)} aoTentar={() => void feed.refetch()} />
              ) : posts.length ? (
                <>
                  {posts.map((c, i) => (
                    <Entrada key={c.id} indice={i < 6 ? i + 4 : 0}>
                      <PostComunicado comunicado={c} />
                    </Entrada>
                  ))}
                  {feed.isFetchingNextPage ? <EsqueletoPost comImagem={false} /> : null}
                  {feed.hasNextPage && !feed.isFetchingNextPage ? (
                    <Botao titulo="Carregar mais comunicados" variante="secundario" icone="chevron-down" onPress={() => void feed.fetchNextPage()} />
                  ) : null}
                </>
              ) : (
                <EstadoVazio icone="newspaper-outline" titulo="Nenhum comunicado disponível" texto="Novos comunicados aparecerão aqui." />
              )}
            </Secao>

            {/* Chamados, embaixo */}
            <Secao icone="chatbubbles-outline" cor={estado.info} titulo="Seus chamados" acao="Ver todos" aoAgir={() => router.navigate('/chamados')}>
              {inicio.isPending ? (
                <>
                  <GrupoEsqueleto>
                    <View className="flex-row flex-wrap gap-3">
                      {[0, 1, 2, 3].map((i) => (
                        <Cartao key={i} style={{ flexBasis: '47%', flexGrow: 1, padding: 14, gap: 8 }}>
                          <Esqueleto largura={30} altura={30} raio={9} />
                          <Esqueleto largura={40} altura={22} />
                          <Esqueleto largura="80%" altura={11} />
                        </Cartao>
                      ))}
                    </View>
                  </GrupoEsqueleto>
                  <EsqueletoCartao />
                </>
              ) : data ? (
                <>
                  <View className="flex-row flex-wrap gap-3">
                    <Numero icone="person-outline" cor={marca[500]} valor={data.chamados.comigo} rotulo="Comigo para resolver" aoTocar={() => router.navigate({ pathname: '/chamados', params: { escopo: 'comigo' } })} />
                    <Numero icone="paper-plane-outline" cor={estado.info} valor={data.chamados.abertos_por_mim} rotulo="Abertos por mim" aoTocar={() => router.navigate({ pathname: '/chamados', params: { escopo: 'meus' } })} />
                    <Numero icone="checkmark-done-outline" cor={estado.alerta} valor={data.chamados.aguardando_minha_aprovacao} rotulo="Aguardando sua aprovação" aoTocar={() => router.navigate({ pathname: '/chamados', params: { escopo: 'meus' } })} />
                    <Numero icone="alarm-outline" cor={estado.perigo} valor={data.chamados.atrasados} rotulo="Atrasados que você vê" aoTocar={() => router.navigate('/chamados')} />
                  </View>
                  {data.chamados_recentes.length ? (
                    data.chamados_recentes.map((c) => <CartaoChamado key={c.id} chamado={c} />)
                  ) : (
                    <Cartao onPress={() => router.push('/chamado/novo')} style={{ padding: 20, alignItems: 'center', gap: 8 }}>
                      <Icone nome="add-circle-outline" tamanho={28} cor={marca[500]} />
                      <Texto variante="corpoForte">Nenhum chamado por aqui</Texto>
                      <Texto variante="legenda" tom="suave">
                        Toque para abrir o primeiro.
                      </Texto>
                    </Cartao>
                  )}
                </>
              ) : null}
            </Secao>
          </>
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

function Secao({
  icone,
  cor,
  titulo,
  subtitulo,
  acao,
  aoAgir,
  children,
}: {
  icone: NomeIcone;
  cor: string;
  titulo: string;
  subtitulo?: React.ReactNode;
  acao?: string;
  aoAgir?: () => void;
  children: React.ReactNode;
}) {
  return (
    <View style={{ gap: 16, paddingHorizontal: 20 }}>
      <View className="flex-row items-center justify-between">
        <View className="flex-1 flex-row items-center gap-2.5">
          <View style={{ width: 30, height: 30, borderRadius: 10, backgroundColor: cor, alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={icone} tamanho={16} cor="#FFFFFF" />
          </View>
          <View className="flex-1">
            <Texto variante="titulo3" accessibilityRole="header">
              {titulo}
            </Texto>
            {subtitulo ? (
              <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                {subtitulo}
              </Texto>
            ) : null}
          </View>
        </View>
        {acao && aoAgir ? (
          <Pressionavel onPress={aoAgir} hitSlop={8} escala={0.94}>
            <Texto variante="legenda" tom="marca" style={{ fontSize: 14 }}>
              {acao}
            </Texto>
          </Pressionavel>
        ) : null}
      </View>
      {children}
    </View>
  );
}

function Numero({ icone, cor, valor, rotulo, aoTocar }: { icone: NomeIcone; cor: string; valor: number; rotulo: string; aoTocar: () => void }) {
  return (
    <View style={{ flexBasis: '47%', flexGrow: 1 }}>
      <Cartao onPress={aoTocar} style={{ padding: 14, gap: 8 }} acessibilidade={`${valor} ${rotulo}`}>
        <View style={{ width: 32, height: 32, borderRadius: 10, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={icone} tamanho={17} cor={cor} />
        </View>
        <Texto variante="titulo2" style={valor > 0 && cor === estado.perigo ? { color: cor } : undefined}>
          {valor}
        </Texto>
        <Texto variante="legenda" tom="suave" numberOfLines={2}>
          {rotulo}
        </Texto>
      </Cartao>
    </View>
  );
}
