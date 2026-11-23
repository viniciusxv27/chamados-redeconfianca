/**
 * Push do portal, depois do login: explica e pede a permissão (uma folha do app
 * antes do pedido do sistema), registra o aparelho, mostra o que chega com o app
 * aberto (atualizando Avisos e o sino) e, ao tocar, abre a tela certa — também
 * com o app fechado (cold start).
 */
import { useQueryClient } from '@tanstack/react-query';
import * as Notifications from 'expo-notifications';
import { type Href, router, useRootNavigationState } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, FadeOut, SlideInDown, SlideOutDown } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Botao, Icone, Texto } from '@/components/ui';
import { marca, raios } from '@/constants/theme';
import { chaves } from '@/hooks/consultas';
import { notificacoes } from '@/services/portal';
import {
  adiarPergunta,
  dadosDaNotificacao,
  deveExplicar,
  pedirPermissao,
  prepararCanal,
  pushDisponivel,
  registrarAparelho,
  situacaoDaPermissao,
} from '@/services/push';
import { useSessao } from '@/providers/sessao';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';
import { abrirLink } from '@/utils/links';

/** Toques já tratados (o do cold start também chega pelo ouvinte em alguns aparelhos). */
const tratadas = new Set<string>();

export function PushDoPortal() {
  if (!pushDisponivel) return null;
  return <Push />;
}

function Push() {
  const { estado, bloqueio, usuario } = useSessao();
  const consultas = useQueryClient();
  const navegacaoPronta = !!useRootNavigationState()?.key;
  const [explicar, setExplicar] = useState(false);
  const logado = estado === 'logado';
  const livre = logado && !bloqueio;

  // Depois do login (não na abertura fria do app sem sessão): registra ou explica e pede.
  useEffect(() => {
    if (!logado) return;
    let vivo = true;
    let espera: ReturnType<typeof setTimeout> | undefined;
    (async () => {
      await prepararCanal();
      const situacao = await situacaoDaPermissao();
      if (!vivo) return;
      if (situacao === 'liberada') {
        await registrarAparelho();
      } else if (situacao === 'perguntar' && (await deveExplicar()) && vivo) {
        // Deixa a primeira tela aparecer antes da folha.
        espera = setTimeout(() => vivo && setExplicar(true), 1200);
      }
    })();
    return () => {
      vivo = false;
      if (espera) clearTimeout(espera);
    };
    // usuario?.id: outra pessoa entrou no mesmo aparelho → registra para ela.
  }, [logado, usuario?.id]);

  const atualizarAvisos = useCallback(() => {
    void consultas.invalidateQueries({ queryKey: chaves.notificacoes });
    void consultas.invalidateQueries({ queryKey: chaves.inicio });
  }, [consultas]);

  const abrir = useCallback(
    (resposta: Notifications.NotificationResponse) => {
      const chave = resposta.notification.request.identifier;
      if (tratadas.has(chave)) return;
      tratadas.add(chave);
      void Notifications.clearLastNotificationResponseAsync().catch(() => undefined);
      if (resposta.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) return;
      const { url, id } = dadosDaNotificacao(resposta.notification);
      if (id) void notificacoes.marcarLida(id).then(atualizarAvisos, () => undefined);
      else atualizarAvisos();
      if (url) abrirLink(url);
      else router.push('/avisos' as Href);
    },
    [atualizarAvisos],
  );

  // Chegou com o app aberto: o handler (services/push) mostra; aqui só atualiza Avisos e o sino.
  useEffect(() => {
    if (!logado) return;
    const recebida = Notifications.addNotificationReceivedListener(atualizarAvisos);
    return () => recebida.remove();
  }, [logado, atualizarAvisos]);

  // Toque na notificação: com o app aberto/em segundo plano (ouvinte) ou fechado (última resposta).
  useEffect(() => {
    if (!livre || !navegacaoPronta) return;
    const tocada = Notifications.addNotificationResponseReceivedListener(abrir);
    let vivo = true;
    void Notifications.getLastNotificationResponseAsync()
      .then((ultima) => {
        // Espera a primeira tela montar antes de empilhar a do aviso.
        if (ultima && vivo) setTimeout(() => vivo && abrir(ultima), 350);
      })
      .catch(() => undefined);
    return () => {
      vivo = false;
      tocada.remove();
    };
  }, [livre, navegacaoPronta, abrir]);

  const ativar = useCallback(async () => {
    setExplicar(false);
    if (await pedirPermissao()) await registrarAparelho();
    else await adiarPergunta();
  }, []);

  const depois = useCallback(() => {
    setExplicar(false);
    void adiarPergunta();
  }, []);

  return <FolhaExplicacao aberta={explicar && logado} aoAtivar={ativar} aoAdiar={depois} />;
}

const ITENS: { icone: 'chatbubbles-outline' | 'megaphone-outline' | 'document-text-outline'; texto: string }[] = [
  { icone: 'chatbubbles-outline', texto: 'Seus chamados andaram ou receberam resposta' },
  { icone: 'megaphone-outline', texto: 'Chegou comunicado novo para você' },
  { icone: 'document-text-outline', texto: 'Documentos, cursos, ponto e demais avisos do portal' },
];

function FolhaExplicacao({ aberta, aoAtivar, aoAdiar }: { aberta: boolean; aoAtivar: () => void; aoAdiar: () => void }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={aberta} transparent animationType="none" onRequestClose={aoAdiar} statusBarTranslucent>
      {aberta ? (
        <View style={{ flex: 1, justifyContent: 'flex-end' }}>
          <Animated.View entering={FadeIn.duration(200)} exiting={FadeOut.duration(150)} style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)' }]}>
            <Pressable style={{ flex: 1 }} onPress={aoAdiar} accessibilityLabel="Fechar" />
          </Animated.View>
          <Animated.View
            entering={SlideInDown.springify().damping(22).stiffness(200)}
            exiting={SlideOutDown.duration(180)}
            style={{
              backgroundColor: cores.fundo,
              borderTopLeftRadius: raios['2xl'],
              borderTopRightRadius: raios['2xl'],
              paddingBottom: insets.bottom + 16,
            }}>
            <View style={{ alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: cores.borda, marginTop: 10 }} />
            <View className="gap-5 px-6 pt-6">
              <View
                style={{
                  width: 56,
                  height: 56,
                  borderRadius: raios.lg,
                  backgroundColor: comAlfa(marca[500], 0.14),
                  alignItems: 'center',
                  justifyContent: 'center',
                }}>
                <Icone nome="notifications" tamanho={28} cor={marca[500]} />
              </View>
              <View className="gap-2">
                <Texto variante="titulo2">Ative as notificações</Texto>
                <Texto tom="suave">Tudo o que aparece no sino do portal chega também no seu celular, mesmo com o app fechado:</Texto>
              </View>
              <View className="gap-3">
                {ITENS.map((item) => (
                  <View key={item.icone} className="flex-row items-center gap-3">
                    <Icone nome={item.icone} tamanho={20} cor={marca[500]} />
                    <Texto className="flex-1">{item.texto}</Texto>
                  </View>
                ))}
              </View>
              <Texto variante="legenda" tom="fraco">
                Você pode mudar isso quando quiser nas configurações do celular.
              </Texto>
              <View className="gap-2">
                <Botao titulo="Ativar notificações" icone="notifications-outline" onPress={aoAtivar} />
                <Botao titulo="Agora não" variante="fantasma" onPress={aoAdiar} />
              </View>
            </View>
          </Animated.View>
        </View>
      ) : null}
    </Modal>
  );
}
