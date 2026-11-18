import * as Haptics from 'expo-haptics';
import { LinearGradient } from 'expo-linear-gradient';
import { ActivityIndicator, Platform, StyleSheet, View } from 'react-native';

import { marca, raios } from '@/constants/theme';

import { Icone, type NomeIcone } from './icone';
import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

/**
 * O botão principal do login do portal: degradê do laranja da marca para o
 * grafite (`from-primary to-gray-800`), cantos de 16 e sombra laranja.
 */
export function BotaoGradiente({ titulo, icone, onPress, carregando, desativado }: { titulo: string; icone?: NomeIcone; onPress: () => void; carregando?: boolean; desativado?: boolean }) {
  return (
    <Pressionavel
      accessibilityLabel={titulo}
      accessibilityState={{ disabled: desativado || carregando, busy: carregando }}
      disabled={desativado || carregando}
      escala={0.97}
      onPress={() => {
        if (Platform.OS !== 'web') void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        onPress();
      }}
      style={{ height: 54, borderRadius: raios.lg, overflow: 'hidden', boxShadow: '0px 10px 24px rgba(255,107,53,0.32)' }}>
      <LinearGradient colors={[marca[500], '#1F2937']} start={{ x: 0, y: 0.5 }} end={{ x: 1, y: 0.5 }} style={StyleSheet.absoluteFill} />
      <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
        {carregando ? (
          <ActivityIndicator color="#FFFFFF" />
        ) : (
          <>
            {icone ? <Icone nome={icone} tamanho={20} cor="#FFFFFF" /> : null}
            <Texto variante="corpoForte" tom="branco" style={{ fontSize: 16 }}>
              {titulo}
            </Texto>
          </>
        )}
      </View>
    </Pressionavel>
  );
}
