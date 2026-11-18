/**
 * Início — a home do portal (templates/home.html) no celular, na mesma ordem:
 * boas-vindas, cartão do perfil (saldo C$ só para quem vê no portal, Mercadinho,
 * Criar comunicado para quem gerencia), comunicados obrigatórios, Comunicados
 * Fixados, Elogios Recentes, Feed de Comunicados com "Carregar mais" — e, por
 * baixo, os chamados da pessoa.
 */
import { router } from 'expo-router';
import { RefreshControl, View } from 'react-native';
import Animated, { Extrapolation, FadeInDown, interpolate, useAnimatedScrollHandler, useAnimatedStyle, useSharedValue } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { CartaoChamado } from '@/components/chamados/cartao-chamado';
import { EsqueletoPost, PostComunicado } from '@/components/comunicados/post-comunicado';
import { CartaoElogio } from '@/components/elogios/cartao-elogio';
import {
  Avatar,
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
import { useFeed, useInicio } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { useSessao } from '@/providers/sessao';
import { useCores } from '@/providers/tema';
import { comAlfa, saudacao } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

export default function Inicio() {
  const { usuario } = useSessao();
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const inicio = useInicio();
  const feed = useFeed();
  const { data } = inicio;
  const pessoa = data?.usuario ?? usuario;
  const posts = feed.data?.pages.flatMap((p) => p.resultados) ?? [];

  // A faixa do topo ganha fundo quando a pessoa rola (o conteúdo não fica por baixo do relógio).
  const rolagem = useSharedValue(0);
  const aoRolar = useAnimatedScrollHandler((e) => {
    rolagem.set(e.contentOffset.y);
  });
  const barra = useAnimatedStyle(() => ({ opacity: interpolate(rolagem.get(), [30, 80], [0, 1], Extrapolation.CLAMP) }));

  function atualizar() {
    void inicio.refetch();
    void feed.refetch();
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
        refreshControl={<RefreshControl refreshing={inicio.isRefetching || (feed.isRefetching && !feed.isFetchingNextPage)} onRefresh={atualizar} tintColor={marca[500]} colors={[marca[500]]} />}>
        {/* Boas-vindas + cartão do perfil */}
        <View style={{ gap: 18, paddingHorizontal: 20 }}>
          <Animated.View entering={FadeInDown.duration(380)}>
            <Texto variante="legenda" tom="fraco">
              {saudacao()} · {new Date().toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' })}
            </Texto>
            <Texto variante="titulo1" numberOfLines={1}>
              Bem-vindo, {pessoa?.primeiro_nome ?? ''}
            </Texto>
          </Animated.View>
          <Entrada indice={1}>
            <CartaoPerfil
              nome={pessoa?.nome ?? ''}
              foto={pessoa?.foto}
              linha={[pessoa?.hierarquia_nome, pessoa?.setor].filter(Boolean).join(' - ')}
              saldo={data?.saldo_cs ?? null}
              podeCriarComunicado={!!data?.pode_criar_comunicado}
            />
          </Entrada>
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

            {/* Elogios Recentes */}
            {inicio.isPending ? (
              <Secao icone="star" cor="#EAB308" titulo="Elogios Recentes">
                <GrupoEsqueleto>
                  <Cartao style={{ padding: 18, flexDirection: 'row', gap: 14 }}>
                    <Esqueleto largura={52} altura={52} raio={16} />
                    <View className="flex-1 gap-2">
                      <Esqueleto largura="80%" />
                      <Esqueleto largura={90} altura={12} />
                      <Esqueleto largura="95%" altura={11} />
                      <Esqueleto largura="70%" altura={11} />
                    </View>
                  </Cartao>
                </GrupoEsqueleto>
              </Secao>
            ) : data?.elogios.length ? (
              <Secao icone="star" cor="#EAB308" titulo="Elogios Recentes" acao="Ver todos" aoAgir={() => abrirNoPortal('/compliments/')}>
                {data.elogios.map((e, i) => (
                  <Entrada key={e.id} indice={i + 3}>
                    <CartaoElogio elogio={e} />
                  </Entrada>
                ))}
              </Secao>
            ) : null}

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

/** O "User Info Card" da home do portal. */
function CartaoPerfil({ nome, foto, linha, saldo, podeCriarComunicado }: { nome: string; foto?: string | null; linha: string; saldo: string | null; podeCriarComunicado: boolean }) {
  return (
    <Cartao style={{ padding: 18, gap: 16 }}>
      <Pressionavel onPress={() => router.navigate('/perfil')} escala={0.98} accessibilityLabel="Abrir perfil" style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
        <Avatar nome={nome || '?'} foto={foto} tamanho={56} borda={marca[500]} />
        <View className="flex-1">
          <Texto variante="titulo3" numberOfLines={1}>
            {nome}
          </Texto>
          {linha ? (
            <Texto variante="legenda" tom="suave" numberOfLines={2}>
              {linha}
            </Texto>
          ) : null}
        </View>
      </Pressionavel>
      <View className="flex-row flex-wrap gap-2">
        {saldo != null ? (
          <View style={{ backgroundColor: marca[500], borderRadius: 999, paddingHorizontal: 14, height: 36, justifyContent: 'center' }}>
            <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.negrito, fontSize: 14 }}>
              C$ {saldo}
            </Texto>
          </View>
        ) : null}
        <Atalho icone="storefront-outline" rotulo="Mercadinho" cor={estado.sucesso} aoTocar={() => abrirNoPortal('/prizes/')} />
        {podeCriarComunicado ? <Atalho icone="add" rotulo="Criar comunicado" cor={estado.info} aoTocar={() => abrirNoPortal('/communications/create/')} /> : null}
      </View>
    </Cartao>
  );
}

function Atalho({ icone, rotulo, cor, aoTocar }: { icone: NomeIcone; rotulo: string; cor: string; aoTocar: () => void }) {
  return (
    <Pressionavel
      onPress={aoTocar}
      vibrar
      escala={0.95}
      accessibilityLabel={rotulo}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6, height: 36, paddingHorizontal: 14, borderRadius: 10, backgroundColor: cor }}>
      <Icone nome={icone} tamanho={16} cor="#FFFFFF" />
      <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.seminegrito, fontSize: 13 }}>
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}

function Secao({ icone, cor, titulo, acao, aoAgir, children }: { icone: NomeIcone; cor: string; titulo: string; acao?: string; aoAgir?: () => void; children: React.ReactNode }) {
  return (
    <View style={{ gap: 16, paddingHorizontal: 20 }}>
      <View className="flex-row items-center justify-between">
        <View className="flex-row items-center gap-2">
          <Icone nome={icone} tamanho={19} cor={cor} />
          <Texto variante="titulo3" accessibilityRole="header">
            {titulo}
          </Texto>
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
