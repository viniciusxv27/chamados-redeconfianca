/**
 * Um post do Feed da rede (/social/ do portal: templates/social/_post.html): quem postou, a foto ou o
 * vídeo no tamanho certo (o espaço fica reservado enquanto carrega), curtir com o coração que pula,
 * a descrição ("mais" quando é longa), os 3 últimos comentários e o "Ver todos". Apagar fica no "…"
 * de quem pode (quem postou e o SUPERADMIN).
 */
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { type Href, router } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useState } from 'react';
import { Alert, Platform, View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSequence, withSpring } from 'react-native-reanimated';

import { Avatar, Cartao, Esqueleto, GrupoEsqueleto, Icone, Pressionavel, Texto } from '@/components/ui';
import { estado, fontes, raios } from '@/constants/theme';
import { useApagarPost, useCurtirPost } from '@/hooks/consultas';
import { useCores } from '@/providers/tema';
import type { PostSocial as Post } from '@/services/tipos';
import { tempoRelativo } from '@/utils/formatar';

export const COR_FEED = ['#FF6B35', '#EC4899'] as const;

/** Pergunta antes de apagar (no navegador, o confirm do próprio navegador). */
export function confirmar(titulo: string, texto: string, aoConfirmar: () => void) {
  if (Platform.OS === 'web') {
    if (globalThis.confirm?.(`${titulo}\n\n${texto}`)) aoConfirmar();
    return;
  }
  Alert.alert(titulo, texto, [
    { text: 'Cancelar', style: 'cancel' },
    { text: 'Apagar', style: 'destructive', onPress: aoConfirmar },
  ]);
}

function Video({ url, proporcao }: { url: string; proporcao: number }) {
  const player = useVideoPlayer(url, (p) => {
    p.loop = false;
  });
  return (
    <VideoView
      player={player}
      nativeControls
      contentFit="contain"
      style={{ width: '100%', aspectRatio: 100 / proporcao, backgroundColor: '#000' }}
    />
  );
}

export function Midia({ post }: { post: Post }) {
  const cores = useCores();
  if (!post.midia) {
    return (
      <View style={{ width: '100%', aspectRatio: 100 / post.proporcao, backgroundColor: cores.superficie2, alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome="image-outline" tamanho={32} cor={cores.texto3} />
      </View>
    );
  }
  if (post.tipo === 'VIDEO') return <Video url={post.midia} proporcao={post.proporcao} />;
  return (
    <Image
      source={{ uri: post.midia }}
      style={{ width: '100%', aspectRatio: 100 / post.proporcao, backgroundColor: cores.superficie2 }}
      contentFit="cover"
      transition={200}
      accessibilityLabel={post.legenda ? `Foto: ${post.legenda}` : `Foto de ${post.autor?.nome ?? 'alguém'}`}
    />
  );
}

export function BotaoCurtirPost({ post }: { post: Post }) {
  const cores = useCores();
  const curtir = useCurtirPost();
  const pulo = useSharedValue(1);
  const estilo = useAnimatedStyle(() => ({ transform: [{ scale: pulo.get() }] }));
  return (
    <Pressionavel
      hitSlop={10}
      accessibilityLabel={post.curti ? 'Descurtir' : 'Curtir'}
      accessibilityState={{ selected: post.curti }}
      onPress={() => {
        if (!post.curti) {
          pulo.set(withSequence(withSpring(1.35, { damping: 8, stiffness: 400 }), withSpring(1, { damping: 12 })));
          if (Platform.OS !== 'web') void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        }
        curtir.mutate(post.id);
      }}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 4 }}>
      <Animated.View style={estilo}>
        <Icone nome={post.curti ? 'heart' : 'heart-outline'} tamanho={24} cor={post.curti ? estado.perigo : cores.texto} />
      </Animated.View>
      <Texto variante="corpoForte" style={{ color: post.curti ? estado.perigo : cores.texto }}>
        {post.curtidas}
      </Texto>
    </Pressionavel>
  );
}

export function CabecalhoPost({ post, aoApagar }: { post: Post; aoApagar?: () => void }) {
  const cores = useCores();
  const apagar = useApagarPost();
  return (
    <View className="flex-row items-center gap-3" style={{ paddingHorizontal: 14, paddingVertical: 12 }}>
      <Avatar nome={post.autor?.nome ?? '?'} foto={post.autor?.foto} tamanho={38} />
      <View className="flex-1">
        <Texto variante="corpoForte" numberOfLines={1} style={{ fontSize: 14 }}>
          {post.autor?.nome ?? 'Alguém'}
        </Texto>
        <Texto variante="legenda" tom="fraco" numberOfLines={1} style={{ fontSize: 12 }}>
          {[post.autor?.setor, tempoRelativo(post.criado_em)].filter(Boolean).join(' · ')}
        </Texto>
      </View>
      {post.pode_apagar ? (
        <Pressionavel
          hitSlop={10}
          escala={0.9}
          disabled={apagar.isPending}
          accessibilityLabel="Apagar publicação"
          onPress={() =>
            confirmar('Apagar publicação?', 'A foto ou o vídeo sai do feed para todo mundo.', () =>
              apagar.mutate(post.id, { onSuccess: () => aoApagar?.() }),
            )
          }
          style={{ padding: 6 }}>
          <Icone nome={apagar.isPending ? 'hourglass-outline' : 'trash-outline'} tamanho={19} cor={cores.texto3} />
        </Pressionavel>
      ) : null}
    </View>
  );
}

export function Legenda({ texto }: { texto: string }) {
  const [aberta, setAberta] = useState(false);
  const [longa, setLonga] = useState(false);
  if (!texto) return null;
  return (
    <View>
      <Texto
        selectable
        numberOfLines={aberta ? undefined : 3}
        onTextLayout={(e) => {
          if (!aberta && e.nativeEvent.lines.length >= 3) setLonga(true);
        }}
        style={{ fontSize: 14, lineHeight: 20 }}>
        {texto}
      </Texto>
      {longa ? (
        <Pressionavel onPress={() => setAberta((a) => !a)} hitSlop={8} escala={0.96} style={{ alignSelf: 'flex-start', marginTop: 2 }}>
          <Texto variante="legenda" tom="fraco" style={{ fontFamily: fontes.seminegrito }}>
            {aberta ? 'menos' : 'mais'}
          </Texto>
        </Pressionavel>
      ) : null}
    </View>
  );
}

/** O post no feed (home e /feed): toca em "comentar" ou "Ver todos" para abrir o post. */
export function PostSocial({ post }: { post: Post }) {
  const cores = useCores();
  const abrir = () => router.push(`/feed/${post.id}` as Href);
  return (
    <Cartao style={{ padding: 0, overflow: 'hidden' }}>
      <CabecalhoPost post={post} />
      <Midia post={post} />
      <View style={{ paddingHorizontal: 14, paddingTop: 10, paddingBottom: 14, gap: 8 }}>
        <View className="flex-row items-center gap-5">
          <BotaoCurtirPost post={post} />
          <Pressionavel onPress={abrir} hitSlop={10} accessibilityLabel="Comentar" style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 4 }}>
            <Icone nome="chatbubble-outline" tamanho={22} cor={cores.texto} />
            <Texto variante="corpoForte">{post.total_comentarios}</Texto>
          </Pressionavel>
        </View>
        <Legenda texto={post.legenda} />
        {post.total_comentarios > post.comentarios.length ? (
          <Pressionavel onPress={abrir} hitSlop={6} escala={0.98} style={{ alignSelf: 'flex-start' }}>
            <Texto variante="legenda" tom="fraco" style={{ fontFamily: fontes.seminegrito }}>
              Ver todos os {post.total_comentarios} comentários
            </Texto>
          </Pressionavel>
        ) : null}
        {post.comentarios.map((c) => (
          <Texto key={c.id} numberOfLines={2} style={{ fontSize: 14, lineHeight: 19 }}>
            <Texto style={{ fontFamily: fontes.negrito, fontSize: 14 }}>{c.autor?.primeiro_nome ?? 'Alguém'} </Texto>
            {c.texto}
          </Texto>
        ))}
        <Pressionavel onPress={abrir} escala={0.98} accessibilityLabel="Escrever um comentário"
          style={{ marginTop: 2, borderRadius: raios.pilula, backgroundColor: cores.superficie2, paddingHorizontal: 14, paddingVertical: 9 }}>
          <Texto tom="fraco" style={{ fontSize: 13 }}>
            Adicione um comentário…
          </Texto>
        </Pressionavel>
      </View>
    </Cartao>
  );
}

export function EsqueletoPostSocial() {
  return (
    <Cartao style={{ padding: 0, overflow: 'hidden' }}>
      <GrupoEsqueleto>
        <View className="flex-row items-center gap-3" style={{ padding: 14 }}>
          <Esqueleto largura={38} altura={38} raio={19} />
          <View className="flex-1 gap-2">
            <Esqueleto largura="50%" />
            <Esqueleto largura="30%" altura={10} />
          </View>
        </View>
        <Esqueleto largura="100%" altura={260} raio={0} />
        <View style={{ padding: 14, gap: 8 }}>
          <Esqueleto largura={90} altura={18} />
          <Esqueleto largura="80%" altura={11} />
        </View>
      </GrupoEsqueleto>
    </Cartao>
  );
}

/** Botão "Publicar" do feed (abre a tela de nova publicação). */
export function BotaoPublicar({ compacto = false }: { compacto?: boolean }) {
  const cores = useCores();
  return (
    <Pressionavel
      onPress={() => router.push('/feed/novo' as Href)}
      escala={0.97}
      vibrar
      accessibilityLabel="Publicar foto ou vídeo no Feed da rede"
      style={{ flexDirection: 'row', alignItems: 'center', gap: 12, borderRadius: raios.lg, borderWidth: 1, borderColor: cores.borda, backgroundColor: cores.superficie, padding: compacto ? 10 : 14 }}>
      <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFF1EB', alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome="camera" tamanho={20} cor={COR_FEED[0]} />
      </View>
      <View className="flex-1">
        <Texto variante="corpoForte" style={{ fontSize: 14 }}>
          Compartilhe um momento
        </Texto>
        <Texto variante="legenda" tom="fraco" style={{ fontSize: 12 }}>
          Foto ou vídeo para a rede toda ver
        </Texto>
      </View>
      <Icone nome="add-circle" tamanho={26} cor={COR_FEED[0]} />
    </Pressionavel>
  );
}
