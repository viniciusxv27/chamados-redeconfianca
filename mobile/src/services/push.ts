/**
 * Push do portal no celular (expo-notifications + Expo Push Service).
 *
 * Tudo o que cai no sino do portal o servidor também manda como push para os
 * aparelhos registrados (api_mobile/push.py). Aqui: canal do Android, permissão,
 * token do Expo e o registro no portal. Nada aqui derruba o app — sem Firebase
 * (google-services.json), no Expo Go do Android ou sem internet, o aparelho só
 * fica sem push.
 */
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { AndroidImportance, AndroidNotificationVisibility, IosAuthorizationStatus } from 'expo-notifications';
import { Platform } from 'react-native';

import { api } from './api';
import { CHAVES, gravar, ler } from './armazenamento';

/** Push só existe no aparelho de verdade (Android/iOS). No navegador e no simulador, não. */
export const pushDisponivel = Platform.OS !== 'web' && Device.isDevice;

/** O mesmo id que o servidor manda em `channelId` (api_mobile/push.py → CANAL_ANDROID). */
const CANAL = 'default';

/** "Agora não" na explicação: perguntar de novo só depois disso. */
const ESPERA_DEPOIS_DO_NAO = 7 * 24 * 60 * 60 * 1000;

if (Platform.OS !== 'web') {
  // Com o app aberto a notificação também aparece (banner + lista), como com ele fechado.
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

/** No Android 13+ o pedido de permissão só aparece depois que existe um canal. */
export async function prepararCanal() {
  if (Platform.OS !== 'android') return;
  try {
    await Notifications.setNotificationChannelAsync(CANAL, {
      name: 'Avisos do portal',
      description: 'Chamados, comunicados, documentos e demais avisos do portal.',
      importance: AndroidImportance.HIGH,
      vibrationPattern: [0, 200, 120, 200],
      lightColor: '#FF6B35',
      lockscreenVisibility: AndroidNotificationVisibility.PUBLIC,
    });
  } catch {
    // sem canal o Android usa o padrão dele
  }
}

export type SituacaoPermissao = 'liberada' | 'perguntar' | 'negada';

export async function situacaoDaPermissao(): Promise<SituacaoPermissao> {
  if (!pushDisponivel) return 'negada';
  try {
    const atual = await Notifications.getPermissionsAsync();
    if (atual.granted || atual.ios?.status === IosAuthorizationStatus.PROVISIONAL) return 'liberada';
    return atual.canAskAgain ? 'perguntar' : 'negada';
  } catch {
    return 'negada';
  }
}

/** A explicação antes do pedido do sistema aparece agora? (não, se ela disse "Agora não" há pouco) */
export async function deveExplicar() {
  const quando = Number((await ler(CHAVES.pushAdiado)) || 0);
  return !quando || Date.now() - quando > ESPERA_DEPOIS_DO_NAO;
}

export async function adiarPergunta() {
  await gravar(CHAVES.pushAdiado, String(Date.now()));
}

/** Abre o pedido do sistema (runtime permission no Android 13+). Devolve se liberou. */
export async function pedirPermissao() {
  if (!pushDisponivel) return false;
  try {
    await prepararCanal();
    const resposta = await Notifications.requestPermissionsAsync({
      ios: { allowAlert: true, allowBadge: true, allowSound: true },
    });
    return resposta.granted;
  } catch {
    return false;
  }
}

function projectId(): string | undefined {
  const extra = Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined;
  return extra?.eas?.projectId ?? Constants.easConfig?.projectId;
}

/**
 * Pega o token do Expo e registra no portal (preso à sessão deste aparelho).
 * Roda a cada abertura com permissão: o token pode mudar e o registro é idempotente.
 * Devolve se registrou.
 */
export async function registrarAparelho() {
  if (!pushDisponivel) return false;
  try {
    const permissao = await Notifications.getPermissionsAsync();
    if (!permissao.granted && permissao.ios?.status !== IosAuthorizationStatus.PROVISIONAL) return false;
    await prepararCanal();
    const id = projectId();
    if (!id) return false;
    // Sem google-services.json (Firebase) no build Android, ou no Expo Go do Android, isto falha: fica sem push.
    const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId: id });
    if (!token) return false;
    await api.post('/push/registrar/', { token, plataforma: Platform.OS });
    return true;
  } catch (erro) {
    if (__DEV__) console.info('[push] aparelho sem push:', (erro as Error)?.message ?? erro);
    return false;
  }
}

/** Ao sair: o portal para de mandar push para este aparelho (o logout também apaga do lado de lá). */
export async function desregistrarAparelho() {
  if (!pushDisponivel) return;
  try {
    await api.post('/push/desregistrar/', undefined, { timeout: 5000 });
  } catch {
    // sem internet: o logout no portal apaga junto quando chegar
  }
}

/** O que o servidor põe em `data` (api_mobile/push.py): link do portal e id do aviso ("p-12"/"c-34"). */
export function dadosDaNotificacao(notificacao: Notifications.Notification) {
  const dados = (notificacao.request.content.data ?? {}) as { url?: unknown; id?: unknown };
  return {
    url: typeof dados.url === 'string' && dados.url ? dados.url : null,
    id: typeof dados.id === 'string' && dados.id ? dados.id : null,
  };
}
