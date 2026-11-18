/**
 * Comunicado como post do "Feed de Comunicados" da home do portal
 * (templates/home.html): autor + data + alfinete, título na cor dele, texto,
 * carrossel de imagens com indicadores, e as ações (curtir, "Estou ciente").
 */
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { router } from 'expo-router';
import { useState } from 'react';
import { FlatList, Platform, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';

import { Avatar, Cartao, Esqueleto, GrupoEsqueleto, Icone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useSituacaoComunicado } from '@/hooks/consultas';
import { useCores } from '@/providers/tema';
import type { ComunicadoResumo } from '@/services/tipos';
import { comAlfa, dataHora } from '@/utils/formatar';

import { BotaoCurtir } from './botao-curtir';

export function PostComunicado({ comunicado }: { comunicado: ComunicadoResumo }) {
  const cores = useCores();
  const abrir = () => router.push({ pathname: '/comunicado/[id]', params: { id: String(comunicado.id) } });
  const cortado = comunicado.texto_feed.endsWith('…');

  return (
    <Cartao style={{ overflow: 'hidden' }}>
      {/* Cabeçalho do post */}
      <Pressionavel onPress={abrir} escala={0.99} accessibilityLabel={`Abrir comunicado ${comunicado.titulo}`}>
        <View className="flex-row items-center gap-3 px-4 pb-3 pt-4">
          <Avatar nome={comunicado.autor?.nome ?? 'Rede Confiança'} foto={comunicado.autor?.foto} tamanho={40} />
          <View className="flex-1">
            <Texto variante="corpoForte" numberOfLines={1}>
              {comunicado.autor?.nome ?? 'Rede Confiança'}
            </Texto>
            <Texto variante="legenda" tom="fraco">
              {dataHora(comunicado.criado_em)}
            </Texto>
          </View>
          {comunicado.fixado ? <Icone nome="pin" tamanho={18} cor={estado.perigo} /> : null}
        </View>
        <View style={{ height: 1, backgroundColor: cores.bordaSuave }} />

        {/* Conteúdo */}
        <View className="gap-2 px-4 pt-3">
          {comunicado.obrigatorio && comunicado.situacao !== 'ESTOU_CIENTE' ? <Selo texto="Leitura obrigatória" cor={estado.perigo} /> : null}
          <Texto variante="titulo3" style={comunicado.cor_titulo && comunicado.cor_titulo !== '#000000' ? { color: comunicado.cor_titulo } : undefined}>
            {comunicado.titulo}
          </Texto>
          {comunicado.texto_feed ? (
            <Texto tom="suave" style={{ lineHeight: 22 }}>
              {comunicado.texto_feed}
              {cortado ? (
                <Texto variante="corpoForte" tom="marca">
                  {'  '}Ler mais
                </Texto>
              ) : null}
            </Texto>
          ) : null}
        </View>
      </Pressionavel>

      {comunicado.imagens.length ? <Carrossel imagens={comunicado.imagens} aoTocar={abrir} /> : <View style={{ height: 12 }} />}

      {/* Ações */}
      <View className="flex-row items-center justify-between px-3 pb-3" style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave, paddingTop: 8 }}>
        <BotaoCurtir id={comunicado.id} curti={comunicado.curti} curtidas={comunicado.curtidas} />
        <Ciencia comunicado={comunicado} />
      </View>
    </Cartao>
  );
}

function Carrossel({ imagens, aoTocar }: { imagens: string[]; aoTocar: () => void }) {
  const cores = useCores();
  const [largura, setLargura] = useState(0);
  const [atual, setAtual] = useState(0);
  return (
    <View onLayout={(e) => setLargura(e.nativeEvent.layout.width)} style={{ marginTop: 12 }}>
      {largura > 0 ? (
        <FlatList
          data={imagens}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          keyExtractor={(u, i) => `${i}-${u}`}
          onMomentumScrollEnd={(e) => setAtual(Math.round(e.nativeEvent.contentOffset.x / largura))}
          renderItem={({ item }) => (
            <Pressionavel onPress={aoTocar} escala={1} accessibilityLabel="Abrir comunicado">
              <Image source={{ uri: item }} style={{ width: largura, aspectRatio: 4 / 3, backgroundColor: cores.superficie3 }} contentFit="cover" transition={250} cachePolicy="memory-disk" />
            </Pressionavel>
          )}
        />
      ) : (
        <View style={{ aspectRatio: 4 / 3, backgroundColor: cores.superficie3 }} />
      )}
      {imagens.length > 1 ? (
        <>
          <View style={{ position: 'absolute', top: 10, right: 10, backgroundColor: 'rgba(0,0,0,0.55)', borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3 }}>
            <Texto variante="legenda" tom="branco" style={{ fontSize: 12 }}>
              {atual + 1}/{imagens.length}
            </Texto>
          </View>
          <View className="flex-row justify-center gap-1.5" style={{ paddingVertical: 10 }}>
            {imagens.map((_, i) => (
              <View key={i} style={{ width: i === atual ? 18 : 6, height: 6, borderRadius: 3, backgroundColor: i === atual ? marca[500] : cores.borda }} />
            ))}
          </View>
        </>
      ) : (
        <View style={{ height: 4 }} />
      )}
    </View>
  );
}

/** "Estou ciente" direto no feed, como os botões do portal; depois vira o selo "Ciente". */
function Ciencia({ comunicado }: { comunicado: ComunicadoResumo }) {
  const situacao = useSituacaoComunicado(comunicado.id);
  if (comunicado.situacao === 'ESTOU_CIENTE') {
    return (
      <Animated.View entering={FadeIn} className="flex-row items-center gap-1.5 px-2">
        <Icone nome="checkmark-circle" tamanho={18} cor={estado.sucesso} />
        <Texto variante="legenda" tom="sucesso" style={{ fontFamily: fontes.seminegrito }}>
          Ciente
        </Texto>
      </Animated.View>
    );
  }
  return (
    <Pressionavel
      onPress={() =>
        situacao.mutate('ESTOU_CIENTE', {
          onSuccess: () => Platform.OS !== 'web' && void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success),
        })
      }
      disabled={situacao.isPending}
      vibrar
      escala={0.95}
      accessibilityLabel="Marcar que estou ciente"
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999, backgroundColor: comAlfa(estado.sucesso, 0.12) }}>
      <Icone nome="checkmark" tamanho={16} cor={estado.sucesso} />
      <Texto variante="legenda" tom="sucesso" style={{ fontFamily: fontes.seminegrito }}>
        {situacao.isPending ? 'Enviando…' : 'Estou ciente'}
      </Texto>
    </Pressionavel>
  );
}

/** Esqueleto do post (cabeçalho + título + linhas + imagem). */
export function EsqueletoPost({ comImagem = true }: { comImagem?: boolean }) {
  return (
    <GrupoEsqueleto>
      <Cartao style={{ overflow: 'hidden' }}>
        <View className="flex-row items-center gap-3 p-4">
          <Esqueleto largura={40} altura={40} raio={20} />
          <View className="flex-1 gap-2">
            <Esqueleto largura="55%" />
            <Esqueleto largura="35%" altura={11} />
          </View>
        </View>
        <View className="gap-2 px-4 pb-4">
          <Esqueleto largura="80%" altura={18} />
          <Esqueleto />
          <Esqueleto largura="90%" />
          <Esqueleto largura="60%" />
        </View>
        {comImagem ? <Esqueleto altura={200} raio={0} /> : null}
      </Cartao>
    </GrupoEsqueleto>
  );
}
