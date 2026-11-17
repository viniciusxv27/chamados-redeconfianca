import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { useLocalSearchParams } from 'expo-router';
import { type ComponentRef, type ReactNode, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, LinearTransition } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { abrirArquivo } from '@/components/modulos/comum';
import { BarraProgresso } from '@/components/modulos/impulso/barra';
import { CartaoConvite } from '@/components/modulos/impulso/cartao-convite';
import { COR_PRIORIDADE_META, COR_STATUS_META, ROTULO_STATUS, dataCurta, textoDoPrazo } from '@/components/modulos/impulso/comum';
import { Avatar, BarraDetalhe, Botao, BotaoIcone, Cartao, Esqueleto, EstadoErro, Icone, type NomeIcone, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useAvaliarMeta, useComentarMeta, useMarcarItem, useMeta, useMoverMeta } from '@/hooks/modulos/impulso';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ComentarioMeta, DetalheMeta, PessoaImpulso, StatusMeta } from '@/services/modulos/impulso';
import { comAlfa, dataHora, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const ICONE_STATUS: Record<StatusMeta, NomeIcone> = {
  A_FAZER: 'ellipse-outline',
  EM_ANDAMENTO: 'play-circle-outline',
  ENTREGUE: 'paper-plane-outline',
  CONCLUIDA: 'checkmark-circle-outline',
};

export default function DetalheMetaImpulso() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const { data, isPending, isError, error, refetch } = useMeta(numero);
  const rolagem = useRef<ComponentRef<typeof ScrollView>>(null);

  return (
    <Tela>
      <BarraDetalhe
        titulo="Meta"
        direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.url_web)} /> : undefined}
      />
      {isPending ? (
        <Carregando />
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView ref={rolagem} contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 24 }} keyboardDismissMode="interactive">
            <Resumo meta={data} />
            {data.meu_convite ? (
              <CartaoConvite
                convite={{
                  id: data.meu_convite.id,
                  meta: { id: data.id, titulo: data.titulo, prazo: data.prazo },
                  dono: data.colaborador,
                  convidado_por: data.meu_convite.convidado_por,
                  mensagem: data.meu_convite.mensagem,
                }}
              />
            ) : null}
            {data.pode_mover ? <Mover meta={data} /> : null}
            <Secao icone="document-text-outline" titulo="Descrição">
              <Texto selectable>{data.descricao || 'Sem descrição.'}</Texto>
              {data.descricao_foto ? (
                <Pressionavel onPress={() => data.descricao_foto && void abrirArquivo(data.descricao_foto)} accessibilityLabel="Abrir a foto da descrição">
                  <Image source={{ uri: data.descricao_foto }} style={{ width: '100%', height: 200, borderRadius: raios.md }} contentFit="cover" transition={200} />
                </Pressionavel>
              ) : null}
              {data.descricao_audio ? (
                <Botao titulo="Ouvir o áudio da descrição" variante="secundario" icone="play" tamanho="sm" onPress={() => data.descricao_audio && void abrirArquivo(data.descricao_audio)} />
              ) : null}
            </Secao>
            {data.itens.length ? <ToDo meta={data} /> : null}
            <Avaliacao meta={data} />
            {data.entrega_link ? (
              <Secao icone="link-outline" titulo="Link da entrega">
                <Texto tom="marca" onPress={() => data.entrega_link && void abrirNoPortal(data.entrega_link)} numberOfLines={2}>
                  {data.entrega_link}
                </Texto>
              </Secao>
            ) : null}
            {data.anexos.length ? (
              <Secao icone="attach" titulo={`Anexos (${data.anexos.length})`}>
                {data.anexos.map((a) => (
                  <Pressionavel
                    key={a.id}
                    onPress={() => a.url && void abrirNoPortal(a.url)}
                    disabled={!a.url}
                    style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}
                    accessibilityLabel={`Abrir ${a.nome}`}>
                    <View style={{ width: 38, height: 38, borderRadius: 10, backgroundColor: comAlfa(marca[500], 0.12), alignItems: 'center', justifyContent: 'center' }}>
                      <Icone nome={a.tipo === 'LINK' ? 'link-outline' : 'document-outline'} tamanho={19} cor={marca[500]} />
                    </View>
                    <View className="flex-1">
                      <Texto variante="corpoForte" numberOfLines={1}>
                        {a.nome}
                      </Texto>
                      <Texto variante="legenda" tom="fraco">
                        {[a.enviado_por?.primeiro_nome, tempoRelativo(a.enviado_em)].filter(Boolean).join(' · ')}
                      </Texto>
                    </View>
                    <Icone nome="open-outline" tamanho={17} cor={marca[500]} />
                  </Pressionavel>
                ))}
              </Secao>
            ) : null}
            {data.parcerias.length ? (
              <Secao icone="people-outline" titulo="Parceria">
                {data.parcerias.map((p) => (
                  <View key={p.id} className="flex-row items-center gap-3">
                    <Avatar nome={p.convidado?.nome ?? '?'} foto={p.convidado?.foto} tamanho={28} />
                    <Texto variante="legenda" className="flex-1" numberOfLines={1}>
                      {p.convidado?.nome ?? '—'}
                    </Texto>
                    <Selo texto={p.status_nome} cor={p.status === 'ACEITA' ? estado.sucesso : p.status === 'RECUSADA' ? estado.perigo : estado.alerta} ponto={false} />
                  </View>
                ))}
              </Secao>
            ) : null}
            <View className="mt-2 gap-3">
              <Texto variante="titulo3">Comentários</Texto>
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

function Resumo({ meta }: { meta: DetalheMeta }) {
  const cores = useCores();
  const concluida = meta.status === 'CONCLUIDA';
  const dias = meta.prazo ? Math.round((new Date(`${meta.prazo}T12:00:00`).getTime() - new Date(new Date().toDateString()).getTime() - 12 * 3600_000) / 86400_000) : null;
  const prazo = textoDoPrazo(dias, concluida);
  const outros = meta.participantes;
  return (
    <Animated.View entering={FadeInDown.duration(350)}>
      <Cartao style={{ padding: 18, gap: 14 }}>
        <View className="flex-row flex-wrap gap-2">
          <Selo texto={meta.status_nome} cor={COR_STATUS_META[meta.status]} />
          <Selo texto={`Prioridade ${meta.prioridade_nome.toLowerCase()}`} cor={COR_PRIORIDADE_META[meta.prioridade]} ponto={false} />
          {meta.recorrencia !== 'UNICA' ? <Selo texto={meta.recorrencia_nome} cor="#8B5CF6" ponto={false} /> : null}
        </View>
        <Texto variante="titulo2" selectable>
          {meta.titulo}
        </Texto>
        {meta.aprovacao !== 'APROVADA' ? (
          <View style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: raios.md, backgroundColor: comAlfa(meta.aprovacao === 'RECUSADA' ? estado.perigo : estado.alerta, 0.12) }}>
            <Icone nome={meta.aprovacao === 'RECUSADA' ? 'close-circle-outline' : 'hourglass-outline'} tamanho={18} cor={meta.aprovacao === 'RECUSADA' ? estado.perigo : estado.alerta} />
            <Texto variante="legenda" className="flex-1">
              {meta.aprovacao === 'RECUSADA'
                ? `Solicitação recusada${meta.motivo_recusa ? `: ${meta.motivo_recusa}` : '.'}`
                : 'Aguardando aprovação do gestor. Enquanto isso a meta não anda no Kanban nem vale pontos.'}
            </Texto>
          </View>
        ) : null}
        <View className="gap-3">
          <LinhaPessoa rotulo="Responsável" pessoa={meta.colaborador} />
          <LinhaPessoa rotulo="Gestor" pessoa={meta.gestor} />
          {outros.length ? <Linha icone="people-outline" rotulo="Junto com" valor={outros.map((p) => p.primeiro_nome).join(', ')} /> : null}
          <Linha
            icone={meta.atrasado ? 'alarm' : 'calendar-outline'}
            rotulo="Prazo"
            valor={meta.prazo ? `${dataCurta(meta.prazo)}${prazo ? ` (${prazo})` : ''}${meta.apenas_dias_uteis ? ' · dias úteis' : ''}` : 'Sem prazo'}
            cor={meta.atrasado ? estado.perigo : undefined}
          />
          {meta.entregue_em ? <Linha icone="paper-plane-outline" rotulo="Entregue em" valor={dataHora(meta.entregue_em)} /> : null}
          {meta.criado_por ? <Linha icone="create-outline" rotulo="Criada por" valor={`${meta.criado_por.nome} · ${tempoRelativo(meta.criado_em)}`} /> : null}
        </View>
        {meta.itens.length ? (
          <View className="gap-1.5">
            <Texto variante="legenda" tom="fraco" style={{ color: cores.texto3 }}>
              {`To-do: ${meta.feitos} de ${meta.itens.length}`}
            </Texto>
            <BarraProgresso pct={(meta.feitos / meta.itens.length) * 100} cor={COR_STATUS_META[meta.status]} />
          </View>
        ) : null}
      </Cartao>
    </Animated.View>
  );
}

/** Mover de coluna, como o arrastar do Kanban. Entregar pede confirmação: a meta vai para a avaliação do gestor. */
function Mover({ meta }: { meta: DetalheMeta }) {
  const mover = useMoverMeta(meta.id);
  const [confirmar, setConfirmar] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  function ir(status: StatusMeta) {
    if (status === 'ENTREGUE' && !confirmar) {
      setConfirmar(true);
      return;
    }
    setAviso(null);
    mover.mutate(status, {
      onSuccess: (r) => {
        setConfirmar(false);
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        if (r.proxima) setAviso(`A próxima ocorrência já está no Kanban, com prazo ${r.proxima}.`);
      },
    });
  }

  return (
    <Secao icone="swap-horizontal" titulo="Mover para">
      <View className="flex-row flex-wrap gap-2">
        {meta.status_possiveis.map((s) => (
          <Botao
            key={s}
            titulo={ROTULO_STATUS[s]}
            icone={ICONE_STATUS[s]}
            tamanho="sm"
            variante={s === 'ENTREGUE' ? 'primario' : 'secundario'}
            larguraTotal={false}
            carregando={mover.isPending && mover.variables === s}
            desativado={mover.isPending}
            onPress={() => ir(s)}
          />
        ))}
      </View>
      {confirmar ? (
        <Animated.View entering={FadeIn} className="gap-2">
          <Texto variante="legenda" tom="suave">
            Marcar como entregue? A meta vai para a avaliação do gestor — a conclusão é ele quem dá.
          </Texto>
          <View className="flex-row gap-2">
            <Botao titulo="Confirmar entrega" icone="paper-plane" tamanho="sm" style={{ flex: 1 }} carregando={mover.isPending} onPress={() => ir('ENTREGUE')} />
            <Botao titulo="Cancelar" variante="secundario" tamanho="sm" style={{ flex: 1 }} desativado={mover.isPending} onPress={() => setConfirmar(false)} />
          </View>
        </Animated.View>
      ) : null}
      {mover.isError ? (
        <Texto variante="legenda" tom="perigo">
          {mensagemDoErro(mover.error)}
        </Texto>
      ) : null}
      {aviso ? (
        <Texto variante="legenda" tom="sucesso">
          {aviso}
        </Texto>
      ) : null}
    </Secao>
  );
}

function ToDo({ meta }: { meta: DetalheMeta }) {
  const cores = useCores();
  const marcar = useMarcarItem(meta.id);
  return (
    <Secao icone="checkbox-outline" titulo={`To-do (${meta.feitos}/${meta.itens.length})`}>
      {meta.itens.map((item) => (
        <Pressionavel
          key={item.id}
          onPress={() => marcar.mutate(item.id)}
          disabled={!meta.pode_marcar_itens}
          vibrar
          escala={0.98}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: item.concluido, disabled: !meta.pode_marcar_itens }}
          accessibilityLabel={item.texto}
          style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingVertical: 4 }}>
          <View
            style={{
              width: 24,
              height: 24,
              borderRadius: 7,
              borderWidth: 2,
              borderColor: item.concluido ? estado.sucesso : cores.borda,
              backgroundColor: item.concluido ? estado.sucesso : 'transparent',
              alignItems: 'center',
              justifyContent: 'center',
              marginTop: -1,
            }}>
            {item.concluido ? <Icone nome="checkmark" tamanho={16} cor="#fff" /> : null}
          </View>
          <View className="flex-1">
            <Texto style={{ color: item.concluido ? cores.texto3 : cores.texto, textDecorationLine: item.concluido ? 'line-through' : 'none' }}>{item.texto}</Texto>
            {item.concluido && item.concluido_por ? (
              <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
                {`por ${item.concluido_por.primeiro_nome}${item.concluido_em ? ` · ${tempoRelativo(item.concluido_em)}` : ''}`}
              </Texto>
            ) : null}
          </View>
        </Pressionavel>
      ))}
      {!meta.pode_marcar_itens ? (
        <Texto variante="legenda" tom="fraco">
          Só quem toca a meta (ou o gestor dela) marca os passos.
        </Texto>
      ) : null}
      {marcar.isError ? (
        <Texto variante="legenda" tom="perigo">
          {mensagemDoErro(marcar.error)}
        </Texto>
      ) : null}
    </Secao>
  );
}

function Avaliacao({ meta }: { meta: DetalheMeta }) {
  const avaliar = useAvaliarMeta(meta.id);
  const cores = useCores();
  const [qualidade, setQualidade] = useState<number | null>(null);
  const [noPrazo, setNoPrazo] = useState<number | null>(null);
  const [comentario, setComentario] = useState('');

  if (meta.avaliacao) {
    const a = meta.avaliacao;
    return (
      <Secao icone="ribbon-outline" titulo="Avaliação do gestor" cor={estado.sucesso}>
        <View className="flex-row gap-3">
          <Nota rotulo="Qualidade" valor={a.nota_qualidade} />
          <Nota rotulo="Prazo" valor={a.nota_prazo} />
        </View>
        {a.comentario ? <Texto selectable>{a.comentario}</Texto> : null}
        <Texto variante="legenda" tom="fraco">
          {[a.avaliado_por ? `Avaliada por ${a.avaliado_por.nome}` : 'Avaliada', a.avaliado_em ? dataHora(a.avaliado_em) : null].filter(Boolean).join(' · ')}
        </Texto>
      </Secao>
    );
  }
  if (!meta.pode_avaliar) {
    if (meta.status !== 'ENTREGUE') return null;
    return (
      <Secao icone="hourglass-outline" titulo="Avaliação do gestor">
        <Texto tom="suave">Entregue. Aguardando a avaliação do gestor.</Texto>
      </Secao>
    );
  }

  const pronto = qualidade != null && noPrazo != null && !avaliar.isPending;
  return (
    <Secao icone="clipboard-outline" titulo="Dar o check (gestor)" cor={estado.sucesso}>
      <EscolhaNota rotulo="Qualidade da entrega" valor={qualidade} aoMudar={setQualidade} />
      <EscolhaNota rotulo="Cumprimento do prazo" valor={noPrazo} aoMudar={setNoPrazo} />
      <TextInput
        value={comentario}
        onChangeText={setComentario}
        placeholder="Comentário (opcional)"
        placeholderTextColor={cores.texto3}
        multiline
        selectionColor={marca[500]}
        accessibilityLabel="Comentário da avaliação"
        style={{ minHeight: 64, borderRadius: raios.md, borderWidth: 1, borderColor: cores.borda, padding: 12, color: cores.texto, fontFamily: fontes.media, fontSize: 15, backgroundColor: cores.superficie2, textAlignVertical: 'top' }}
      />
      {avaliar.isError ? (
        <Texto variante="legenda" tom="perigo">
          {mensagemDoErro(avaliar.error)}
        </Texto>
      ) : null}
      <Botao
        titulo="Dar o check e concluir"
        icone="checkmark-done"
        desativado={!pronto}
        carregando={avaliar.isPending}
        onPress={() =>
          qualidade != null &&
          noPrazo != null &&
          avaliar.mutate(
            { nota_qualidade: qualidade, nota_prazo: noPrazo, comentario: comentario.trim() || undefined },
            {
              onSuccess: () => {
                if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
              },
            },
          )
        }
      />
    </Secao>
  );
}

function EscolhaNota({ rotulo, valor, aoMudar }: { rotulo: string; valor: number | null; aoMudar: (n: number) => void }) {
  const cores = useCores();
  return (
    <View className="gap-2">
      <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
        {`${rotulo} (0 a 5)`}
      </Texto>
      <View className="flex-row gap-2">
        {[0, 1, 2, 3, 4, 5].map((n) => {
          const ativo = valor === n;
          return (
            <Pressionavel
              key={n}
              onPress={() => aoMudar(n)}
              vibrar
              escala={0.9}
              accessibilityRole="radio"
              accessibilityState={{ selected: ativo }}
              accessibilityLabel={`${rotulo}: ${n}`}
              style={{ flex: 1, height: 40, borderRadius: raios.sm, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: ativo ? estado.sucesso : cores.borda, backgroundColor: ativo ? comAlfa(estado.sucesso, 0.14) : cores.superficie }}>
              <Texto variante="corpoForte" style={{ color: ativo ? estado.sucesso : cores.texto2 }}>
                {String(n)}
              </Texto>
            </Pressionavel>
          );
        })}
      </View>
    </View>
  );
}

function Nota({ rotulo, valor }: { rotulo: string; valor: number }) {
  const cores = useCores();
  return (
    <View style={{ flex: 1, padding: 12, borderRadius: raios.md, backgroundColor: cores.superficie2, alignItems: 'center', gap: 2 }}>
      <Texto variante="legenda" tom="fraco">
        {rotulo}
      </Texto>
      <Texto variante="titulo2">{`${valor}/5`}</Texto>
    </View>
  );
}

function Linha({ icone, rotulo, valor, cor }: { icone: NomeIcone; rotulo: string; valor: string; cor?: string }) {
  const cores = useCores();
  return (
    <View className="flex-row items-start gap-3">
      <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto3} style={{ marginTop: 1 }} />
      <Texto variante="legenda" tom="fraco" style={{ width: 92 }}>
        {rotulo}
      </Texto>
      <Texto variante="legenda" className="flex-1" style={{ fontFamily: fontes.seminegrito, color: cor ?? cores.texto }}>
        {valor}
      </Texto>
    </View>
  );
}

function LinhaPessoa({ rotulo, pessoa }: { rotulo: string; pessoa: PessoaImpulso | null }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-3">
      <Icone nome="person-outline" tamanho={18} cor={cores.texto3} />
      <Texto variante="legenda" tom="fraco" style={{ width: 92 }}>
        {rotulo}
      </Texto>
      <View className="flex-1 flex-row items-center gap-2">
        {pessoa ? <Avatar nome={pessoa.nome} foto={pessoa.foto} tamanho={22} /> : null}
        <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito, color: cores.texto }} numberOfLines={1} className="flex-1">
          {pessoa?.nome ?? '—'}
        </Texto>
      </View>
    </View>
  );
}

function Secao({ icone, titulo, cor, children }: { icone: NomeIcone; titulo: string; cor?: string; children: ReactNode }) {
  const cores = useCores();
  return (
    <Animated.View entering={FadeInDown.delay(80).duration(350)}>
      <Cartao style={{ padding: 18, gap: 12, borderColor: cor ? comAlfa(cor, 0.35) : undefined }}>
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

function Mensagem({ comentario, indice }: { comentario: ComentarioMeta; indice: number }) {
  const cores = useCores();
  const meu = comentario.sou_eu;
  return (
    <Animated.View
      entering={FadeInDown.delay(Math.min(indice, 6) * 40).springify().damping(20)}
      layout={LinearTransition}
      className={`flex-row items-end gap-2 ${meu ? 'flex-row-reverse' : ''}`}>
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
  const comentar = useComentarMeta(id);
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
      </Cartao>
    </View>
  );
}
