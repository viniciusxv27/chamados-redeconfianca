/**
 * Guarda pequenos valores do app. No celular, o token vai para o cofre do
 * sistema (Keychain no iOS, Keystore no Android); no navegador (só para
 * desenvolvimento), cai no localStorage.
 */
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const naWeb = Platform.OS === 'web';

export async function ler(chave: string): Promise<string | null> {
  try {
    if (naWeb) return globalThis.localStorage?.getItem(chave) ?? null;
    return await SecureStore.getItemAsync(chave);
  } catch {
    return null;
  }
}

export async function gravar(chave: string, valor: string): Promise<void> {
  try {
    if (naWeb) globalThis.localStorage?.setItem(chave, valor);
    else await SecureStore.setItemAsync(chave, valor);
  } catch {
    // sem cofre disponível: segue só com a memória desta sessão
  }
}

export async function apagar(chave: string): Promise<void> {
  try {
    if (naWeb) globalThis.localStorage?.removeItem(chave);
    else await SecureStore.deleteItemAsync(chave);
  } catch {
    // nada a fazer
  }
}

export const CHAVES = {
  token: 'rc.token',
  tema: 'rc.tema',
  /** E-mail lembrado na tela de entrar ("Lembrar meu e-mail"). Nunca a senha. */
  email: 'rc.email',
} as const;
