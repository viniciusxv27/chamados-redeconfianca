import { router } from 'expo-router';
import type { ReactNode } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { movimento } from '@/constants/theme';
import { useTema } from '@/providers/tema';

import { Icone, type NomeIcone } from './icone';
import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

/** Fundo da tela (cinza claro / grafite), respeitando a área segura do topo quando pedido. */
export function Tela({ children, topo = false, style }: { children: ReactNode; topo?: boolean; style?: StyleProp<ViewStyle> }) {
  const insets = useSafeAreaInsets();
  const { cores } = useTema();
  return <View style={[{ flex: 1, backgroundColor: cores.fundo, paddingTop: topo ? insets.top : 0 }, style]}>{children}</View>;
}

/** Título grande das abas (estilo iOS), com ação opcional à direita. */
export function CabecalhoGrande({ titulo, subtitulo, direita }: { titulo: string; subtitulo?: string; direita?: ReactNode }) {
  return (
    <View className="flex-row items-end justify-between px-5 pb-3 pt-2">
      <View className="flex-1">
        {subtitulo ? (
          <Texto variante="rotulo" tom="fraco" className="mb-1">
            {subtitulo}
          </Texto>
        ) : null}
        <Texto variante="titulo1" accessibilityRole="header">
          {titulo}
        </Texto>
      </View>
      {direita}
    </View>
  );
}

/** Botão redondo de ícone (voltar, ações do cabeçalho). */
export function BotaoIcone({ icone, onPress, rotulo, cor, fundo }: { icone: NomeIcone; onPress: () => void; rotulo: string; cor?: string; fundo?: string }) {
  const { cores } = useTema();
  return (
    <Pressionavel
      onPress={onPress}
      escala={0.9}
      vibrar
      hitSlop={8}
      accessibilityLabel={rotulo}
      style={{ width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center', backgroundColor: fundo ?? cores.superficie2 }}>
      <Icone nome={icone} tamanho={21} cor={cor ?? cores.texto} />
    </Pressionavel>
  );
}

/** Barra das telas de detalhe: voltar + título curto + ação. */
export function BarraDetalhe({ titulo, direita }: { titulo?: string; direita?: ReactNode }) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-row items-center gap-3 px-4 pb-2" style={{ paddingTop: insets.top + 6 }}>
      <BotaoIcone icone="chevron-back" rotulo="Voltar" onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} />
      <Texto variante="corpoForte" className="flex-1" numberOfLines={1}>
        {titulo ?? ''}
      </Texto>
      {direita ?? <View style={{ width: 40 }} />}
    </View>
  );
}

/** Entrada em cascata para itens de lista (sobe e aparece, com mola). */
export function Entrada({ indice = 0, children, style }: { indice?: number; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <Animated.View entering={FadeInDown.delay(Math.min(indice, 8) * movimento.cascata).springify().damping(20).stiffness(180)} style={style}>
      {children}
    </Animated.View>
  );
}
