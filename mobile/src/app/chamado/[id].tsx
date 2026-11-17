import * as Haptics from 'expo-haptics';
import { useLocalSearchParams } from 'expo-router';
import { type ComponentRef, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, LinearTransition, ZoomIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import {
  Avatar,
  BarraDetalhe,
  BotaoIcone,
  Cartao,
  Esqueleto,
  EstadoErro,
  Icone,
  type NomeIcone,
  Pressionavel,
  Selo,
  SeloPrioridade,
  SeloStatus,
  Tela,
  Texto,
} from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useChamado, useComentar } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import type { Chamado, Comentario } from '@/services/tipos';
import { useCores } from '@/providers/tema';
import { comAlfa, dataHora, prazo, tamanhoArquivo, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

export default function DetalheChamado() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const { data, isPending, isError, error, refetch } = useChamado(numero);
  const rolagem = useRef<ComponentRef<typeof ScrollView>>(null);

  return (
    <Tela>
      <BarraDetalhe
        titulo={data ? `Chamado #${data.id}` : `Chamado #${id}`}
        direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.url_web)} /> : undefined}
      />
      {isPending ? (
        <Carregando />
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView
            ref={rolagem}
            contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 24 }}
            keyboardDismissMode="interactive"
            onContentSizeChange={() => undefined}>
            <Resumo chamado={data} />
            <Secao icone="document-text-outline" titulo="Descrição">
              <Texto selectable>{data.descricao}</Texto>
            </Secao>
            {data.solucao ? (
              <Secao icone="checkmark-circle-outline" titulo="Solução" cor={estado.sucesso}>
                <Texto selectable>{data.solucao}</Texto>
              </Secao>
            ) : null}
            {data.anexos.length ? (
              <Secao icone="attach" titulo={`Anexos (${data.anexos.length})`}>
                <View className="gap-2">
                  {data.anexos.map((a) => (
                    <Pressionavel
                      key={a.id}
                      onPress={() => a.url && abrirNoPortal(a.url)}
                      disabled={!a.url}
                      style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}
                      accessibilityLabel={`Abrir anexo ${a.nome}`}>
                      <IconeArquivo tipo={a.tipo} />
                      <View className="flex-1">
                        <Texto variante="corpoForte" numberOfLines={1}>
                          {a.nome}
                        </Texto>
                        <Texto variante="legenda" tom="fraco">
                          {tamanhoArquivo(a.tamanho)} · {tempoRelativo(a.enviado_em)}
                        </Texto>
                      </View>
                      <Icone nome="download-outline" tamanho={18} cor={marca[500]} />
                    </Pressionavel>
                  ))}
                </View>
              </Secao>
            ) : null}

            <View className="mt-2 gap-3">
              <Texto variante="titulo3">Conversa</Texto>
              {data.comentarios.length ? (
                data.comentarios.map((c, i) => <Mensagem key={c.id} comentario={c} indice={i} />)
              ) : (
                <Texto tom="fraco">Ninguém comentou ainda.</Texto>
              )}
            </View>
          </ScrollView>
          {data.pode_comentar ? <Compositor id={data.id} aoEnviar={() => setTimeout(() => rolagem.current?.scrollToEnd({ animated: true }), 120)} /> : null}
        </KeyboardAvoidingView>
      )}
    </Tela>
  );
}

function Resumo({ chamado }: { chamado: Chamado }) {
  const cores = useCores();
  const textoPrazo = chamado.em_aberto ? prazo(chamado.prazo) : null;
  return (
    <Animated.View entering={FadeInDown.duration(350)}>
      <Cartao style={{ padding: 18, gap: 14 }}>
        <View className="flex-row flex-wrap gap-2">
          <SeloStatus status={chamado.status} nome={chamado.status_nome} />
          <SeloPrioridade prioridade={chamado.prioridade} nome={`Prioridade ${chamado.prioridade_nome.toLowerCase()}`} />
          {chamado.tags.map((t) => (
            <Selo key={t.nome} texto={t.nome} cor={t.cor || cores.texto3} ponto={false} />
          ))}
        </View>
        <Texto variante="titulo2" selectable>
          {chamado.titulo}
        </Texto>
        <View className="gap-3">
          <Linha icone="business-outline" rotulo="Setor" valor={[chamado.setor, chamado.categoria].filter(Boolean).join(' · ') || '—'} />
          {chamado.local ? <Linha icone="location-outline" rotulo="Loja/local" valor={chamado.local} /> : null}
          <LinhaPessoa rotulo="Aberto por" nome={chamado.solicitante?.nome} foto={chamado.solicitante?.foto} />
          <LinhaPessoa rotulo="Responsável" nome={chamado.responsavel?.nome ?? 'Ainda sem responsável'} foto={chamado.responsavel?.foto} vazio={!chamado.responsavel} />
          {chamado.auxiliares.length ? <Linha icone="people-outline" rotulo="Também atendem" valor={chamado.auxiliares.map((p) => p.primeiro_nome).join(', ')} /> : null}
          <Linha icone="calendar-outline" rotulo="Aberto em" valor={dataHora(chamado.criado_em)} />
          {textoPrazo ? <Linha icone={chamado.atrasado ? 'alarm' : 'time-outline'} rotulo="Prazo" valor={`${dataHora(chamado.prazo)} (${textoPrazo})`} cor={chamado.atrasado ? estado.perigo : undefined} /> : null}
        </View>
      </Cartao>
    </Animated.View>
  );
}

function Linha({ icone, rotulo, valor, cor }: { icone: NomeIcone; rotulo: string; valor: string; cor?: string }) {
  const cores = useCores();
  return (
    <View className="flex-row items-start gap-3">
      <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto3} style={{ marginTop: 1 }} />
      <Texto variante="legenda" tom="fraco" style={{ width: 96 }}>
        {rotulo}
      </Texto>
      <Texto variante="legenda" className="flex-1" style={{ fontFamily: fontes.seminegrito, color: cor ?? cores.texto }}>
        {valor}
      </Texto>
    </View>
  );
}

function LinhaPessoa({ rotulo, nome, foto, vazio }: { rotulo: string; nome?: string; foto?: string | null; vazio?: boolean }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-3">
      <Icone nome="person-outline" tamanho={18} cor={cores.texto3} />
      <Texto variante="legenda" tom="fraco" style={{ width: 96 }}>
        {rotulo}
      </Texto>
      <View className="flex-1 flex-row items-center gap-2">
        {!vazio && nome ? <Avatar nome={nome} foto={foto} tamanho={22} /> : null}
        <Texto variante="legenda" tom={vazio ? 'fraco' : 'padrao'} style={{ fontFamily: fontes.seminegrito }} numberOfLines={1} className="flex-1">
          {nome ?? '—'}
        </Texto>
      </View>
    </View>
  );
}

function Secao({ icone, titulo, cor, children }: { icone: NomeIcone; titulo: string; cor?: string; children: React.ReactNode }) {
  const cores = useCores();
  return (
    <Animated.View entering={FadeInDown.delay(80).duration(350)}>
      <Cartao style={{ padding: 18, gap: 10, borderColor: cor ? comAlfa(cor, 0.35) : undefined }}>
        <View className="flex-row items-center gap-2">
          <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto2} />
          <Texto variante="rotulo" tom="suave">
            {titulo}
          </Texto>
        </View>
        {children}
      </Cartao>
    </Animated.View>
  );
}

function IconeArquivo({ tipo }: { tipo: string }) {
  const nome: NomeIcone = tipo.startsWith('image/') ? 'image-outline' : tipo.includes('pdf') ? 'document-text-outline' : tipo.startsWith('video/') ? 'videocam-outline' : 'document-outline';
  return (
    <View style={{ width: 40, height: 40, borderRadius: 10, backgroundColor: comAlfa(marca[500], 0.12), alignItems: 'center', justifyContent: 'center' }}>
      <Icone nome={nome} tamanho={20} cor={marca[500]} />
    </View>
  );
}

/** Comentários como conversa: os seus à direita em laranja; eventos (status, atribuição) no meio. */
function Mensagem({ comentario, indice }: { comentario: Comentario; indice: number }) {
  const cores = useCores();
  const evento = comentario.tipo !== 'COMMENT' && comentario.tipo !== 'FOLLOW_UP';
  const atraso = Math.min(indice, 6) * 40;

  if (evento) {
    return (
      <Animated.View entering={FadeIn.delay(atraso)} className="items-center py-1">
        <View className="flex-row items-center gap-2 rounded-full px-3 py-1.5" style={{ backgroundColor: cores.superficie2 }}>
          <Icone nome="git-commit-outline" tamanho={14} cor={cores.texto3} />
          <Texto variante="legenda" tom="suave" style={{ maxWidth: 280 }}>
            {comentario.autor?.primeiro_nome ? `${comentario.autor.primeiro_nome}: ` : ''}
            {comentario.texto}
          </Texto>
        </View>
        <Texto variante="legenda" tom="fraco" style={{ fontSize: 11, marginTop: 2 }}>
          {comentario.tipo_nome} · {tempoRelativo(comentario.criado_em)}
        </Texto>
      </Animated.View>
    );
  }

  const meu = comentario.sou_eu;
  return (
    <Animated.View entering={FadeInDown.delay(atraso).springify().damping(20)} layout={LinearTransition} className={`flex-row items-end gap-2 ${meu ? 'flex-row-reverse' : ''}`}>
      {!meu ? <Avatar nome={comentario.autor?.nome ?? '?'} foto={comentario.autor?.foto} tamanho={28} /> : null}
      <View style={{ maxWidth: '80%', gap: 4 }}>
        <View
          style={{
            backgroundColor: meu ? marca[500] : cores.superficie,
            borderRadius: raios.lg,
            borderBottomRightRadius: meu ? 6 : raios.lg,
            borderBottomLeftRadius: meu ? raios.lg : 6,
            paddingHorizontal: 14,
            paddingVertical: 10,
            borderWidth: meu ? 0 : 1,
            borderColor: cores.bordaSuave,
          }}>
          {!meu ? (
            <Texto variante="legenda" tom="marca" style={{ fontFamily: fontes.negrito, marginBottom: 2 }}>
              {comentario.autor?.nome ?? 'Alguém'}
            </Texto>
          ) : null}
          <Texto selectable style={meu ? { color: '#fff' } : undefined}>
            {comentario.texto}
          </Texto>
        </View>
        <Texto variante="legenda" tom="fraco" style={{ fontSize: 11, alignSelf: meu ? 'flex-end' : 'flex-start' }}>
          {tempoRelativo(comentario.criado_em)}
        </Texto>
      </View>
    </Animated.View>
  );
}

function Compositor({ id, aoEnviar }: { id: number; aoEnviar: () => void }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const comentar = useComentar(id);
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
          selectionColor={marca[500]}
          accessibilityLabel="Comentário"
          style={{ flex: 1, maxHeight: 120, minHeight: 44, borderRadius: 22, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12, backgroundColor: cores.superficie2, color: cores.texto, fontFamily: fontes.media, fontSize: 15, ...(Platform.OS === 'web' ? { outlineWidth: 0 } : null) }}
        />
        <Animated.View entering={ZoomIn}>
          <Pressionavel
            onPress={enviar}
            disabled={!pronto}
            escala={0.88}
            accessibilityLabel="Enviar comentário"
            style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: pronto ? marca[500] : cores.superficie3, alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={comentar.isPending ? 'hourglass-outline' : 'send'} tamanho={19} cor={pronto ? '#fff' : cores.texto3} />
          </Pressionavel>
        </Animated.View>
      </View>
    </View>
  );
}

function Carregando() {
  return (
    <View className="gap-4 p-5 pt-2">
      <Cartao style={{ padding: 18, gap: 14 }}>
        <View className="flex-row gap-2">
          <Esqueleto largura={96} altura={24} raio={12} />
          <Esqueleto largura={120} altura={24} raio={12} />
        </View>
        <Esqueleto largura="85%" altura={22} />
        {[0, 1, 2, 3].map((i) => (
          <Esqueleto key={i} largura={`${70 - i * 8}%`} altura={13} />
        ))}
      </Cartao>
      <Cartao style={{ padding: 18, gap: 10 }}>
        <Esqueleto largura={100} altura={12} />
        <Esqueleto />
        <Esqueleto largura="90%" />
        <Esqueleto largura="60%" />
      </Cartao>
    </View>
  );
}
