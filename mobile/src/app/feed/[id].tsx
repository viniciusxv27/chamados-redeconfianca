/** Um post do Feed da rede com todos os comentários (o link do aviso "Novo comentário na sua publicação"). */
import * as Haptics from 'expo-haptics';
import { router, useLocalSearchParams } from 'expo-router';
import { type ComponentRef, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, LinearTransition } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BotaoCurtirPost, CabecalhoPost, confirmar, EsqueletoPostSocial, Legenda, Midia } from '@/components/social/post-social';
import { Avatar, BarraDetalhe, Cartao, EstadoErro, Icone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useApagarComentario, useComentarPost, usePostSocial } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { useCores } from '@/providers/tema';
import type { ComentarioSocial } from '@/services/tipos';
import { tempoRelativo } from '@/utils/formatar';

export default function PostDoFeed() {
  const { id: param } = useLocalSearchParams<{ id: string }>();
  const id = Number(param) || 0;
  const cores = useCores();
  const { data, isPending, isError, error, refetch } = usePostSocial(id);
  const rolagem = useRef<ComponentRef<typeof ScrollView>>(null);

  return (
    <Tela>
      <BarraDetalhe titulo="Publicação" />
      {isPending ? (
        <View style={{ padding: 16 }}>
          <EsqueletoPostSocial />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView ref={rolagem} contentContainerStyle={{ padding: 16, paddingTop: 4, gap: 16, paddingBottom: 24 }} keyboardDismissMode="interactive">
            <Cartao style={{ padding: 0, overflow: 'hidden' }}>
              <CabecalhoPost post={data} aoApagar={() => router.back()} />
              <Midia post={data} />
              <View style={{ paddingHorizontal: 14, paddingTop: 10, paddingBottom: 14, gap: 8 }}>
                <View className="flex-row items-center gap-5">
                  <BotaoCurtirPost post={data} />
                  <View className="flex-row items-center gap-1.5">
                    <Icone nome="chatbubble-outline" tamanho={22} cor={cores.texto} />
                    <Texto variante="corpoForte">{data.total_comentarios}</Texto>
                  </View>
                </View>
                <Legenda texto={data.legenda} />
              </View>
            </Cartao>
            <View className="gap-3">
              <Texto variante="titulo3">Comentários</Texto>
              {data.comentarios.length ? (
                data.comentarios.map((c, i) => <Comentario key={c.id} comentario={c} postId={data.id} indice={i} />)
              ) : (
                <Texto tom="fraco">Ninguém comentou ainda. Seja o primeiro!</Texto>
              )}
            </View>
          </ScrollView>
          <Compositor id={data.id} aoEnviar={() => setTimeout(() => rolagem.current?.scrollToEnd({ animated: true }), 120)} />
        </KeyboardAvoidingView>
      )}
    </Tela>
  );
}

function Comentario({ comentario, postId, indice }: { comentario: ComentarioSocial; postId: number; indice: number }) {
  const cores = useCores();
  const apagar = useApagarComentario(postId);
  return (
    <Animated.View entering={FadeInDown.delay(Math.min(indice, 6) * 40).springify().damping(20)} layout={LinearTransition} className="flex-row items-start gap-2.5">
      <Avatar nome={comentario.autor?.nome ?? '?'} foto={comentario.autor?.foto} tamanho={32} />
      <View className="flex-1" style={{ backgroundColor: cores.superficie, borderRadius: 16, borderTopLeftRadius: 6, borderWidth: 1, borderColor: cores.bordaSuave, paddingHorizontal: 12, paddingVertical: 8 }}>
        <View className="flex-row items-center gap-2">
          <Texto variante="legenda" numberOfLines={1} className="flex-1" style={{ fontFamily: fontes.negrito, color: cores.texto }}>
            {comentario.autor?.nome ?? 'Alguém'}
          </Texto>
          <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
            {tempoRelativo(comentario.criado_em)}
          </Texto>
          {comentario.pode_apagar ? (
            <Pressionavel
              hitSlop={10}
              disabled={apagar.isPending}
              accessibilityLabel="Apagar comentário"
              onPress={() => confirmar('Apagar comentário?', 'O comentário sai da publicação.', () => apagar.mutate(comentario.id))}>
              <Icone nome="trash-outline" tamanho={15} cor={cores.texto3} />
            </Pressionavel>
          ) : null}
        </View>
        <Texto selectable style={{ fontSize: 14, lineHeight: 20, marginTop: 2 }}>
          {comentario.texto}
        </Texto>
      </View>
    </Animated.View>
  );
}

function Compositor({ id, aoEnviar }: { id: number; aoEnviar: () => void }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const comentar = useComentarPost(id);
  const [texto, setTexto] = useState('');
  const pronto = texto.trim().length > 0 && !comentar.isPending;

  function enviar() {
    if (!pronto) return;
    comentar.mutate(texto.trim(), {
      onSuccess: () => {
        setTexto('');
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        aoEnviar();
      },
    });
  }

  return (
    <View style={{ borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie, paddingHorizontal: 12, paddingTop: 10, paddingBottom: Math.max(insets.bottom, 10) }}>
      {comentar.isError ? (
        <Animated.View entering={FadeIn} className="mb-2 flex-row items-center gap-2 px-2">
          <Icone nome="alert-circle" tamanho={16} cor={estado.perigo} />
          <Texto variante="legenda" tom="perigo">
            {mensagemDoErro(comentar.error)}
          </Texto>
        </Animated.View>
      ) : null}
      <View className="flex-row items-end gap-2">
        <TextInput
          value={texto}
          onChangeText={setTexto}
          placeholder="Escreva um comentário…"
          placeholderTextColor={cores.texto3}
          multiline
          maxLength={1000}
          selectionColor={marca[500]}
          accessibilityLabel="Comentário"
          style={{ flex: 1, maxHeight: 120, minHeight: 44, borderRadius: 22, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12, backgroundColor: cores.superficie2, color: cores.texto, fontFamily: fontes.media, fontSize: 15, ...(Platform.OS === 'web' ? { outlineWidth: 0 } : null) }}
        />
        <Pressionavel
          onPress={enviar}
          disabled={!pronto}
          escala={0.88}
          accessibilityLabel="Enviar comentário"
          style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: pronto ? marca[500] : cores.superficie3, alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={comentar.isPending ? 'hourglass-outline' : 'send'} tamanho={19} cor={pronto ? '#fff' : cores.texto3} />
        </Pressionavel>
      </View>
    </View>
  );
}
