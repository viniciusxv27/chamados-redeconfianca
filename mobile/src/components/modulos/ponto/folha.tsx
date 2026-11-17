/** Folha de baixo para cima para as ações com texto (aprovar, devolver, cancelar). */
import type { ReactNode } from 'react';
import { KeyboardAvoidingView, Modal, Platform, Pressable, Switch, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Texto } from '@/components/ui';
import { marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';

export function Folha({ aberta, titulo, aoFechar, children }: { aberta: boolean; titulo: string; aoFechar: () => void; children: ReactNode }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={aberta} transparent animationType="slide" onRequestClose={aoFechar} statusBarTranslucent>
      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)' }} onPress={aoFechar} accessibilityLabel="Fechar" />
        <View
          style={{
            backgroundColor: cores.fundo,
            borderTopLeftRadius: raios['2xl'],
            borderTopRightRadius: raios['2xl'],
            paddingHorizontal: 20,
            paddingTop: 10,
            paddingBottom: Math.max(insets.bottom, 16) + 4,
            gap: 14,
          }}>
          <View style={{ alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: cores.borda }} />
          <Texto variante="titulo3">{titulo}</Texto>
          {children}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

export function Alternar({ rotulo, valor, aoMudar }: { rotulo: string; valor: boolean; aoMudar: (v: boolean) => void }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-3">
      <Texto variante="legenda" tom="suave" className="flex-1">
        {rotulo}
      </Texto>
      <Switch value={valor} onValueChange={aoMudar} trackColor={{ true: marca[500], false: cores.superficie3 }} thumbColor="#FFFFFF" accessibilityLabel={rotulo} />
    </View>
  );
}
