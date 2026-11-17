import { Stack } from 'expo-router';

import { useCores } from '@/providers/tema';

/**
 * Pilha dos módulos do portal (Impulso, Ponto, Contracheque…). Cada módulo tem
 * a sua pasta: src/app/modulo/<chave>/index.tsx e as telas de detalhe dele.
 * Fica dentro do Stack.Protected da raiz — só abre logado e sem bloqueio.
 */
export default function LayoutModulos() {
  const cores = useCores();
  return <Stack screenOptions={{ headerShown: false, animation: 'ios_from_right', contentStyle: { backgroundColor: cores.fundo } }} />;
}
