import { useState } from 'react';
import { View, type LayoutChangeEvent } from 'react-native';
import Animated, { useAnimatedStyle, withSpring } from 'react-native-reanimated';

import { fontes, movimento, raios } from '@/constants/theme';
import { useTema } from '@/providers/tema';

import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

/** Abas pequenas com o indicador deslizando por baixo (mola). */
export function Segmentado<T extends string>({ opcoes, valor, aoMudar }: { opcoes: { valor: T; rotulo: string }[]; valor: T; aoMudar: (v: T) => void }) {
  const { cores, escuro } = useTema();
  const [largura, setLargura] = useState(0);
  const indice = Math.max(0, opcoes.findIndex((o) => o.valor === valor));
  const larguraItem = largura / opcoes.length;
  const indicador = useAnimatedStyle(() => ({
    width: larguraItem,
    transform: [{ translateX: withSpring(indice * larguraItem, movimento.mola) }],
  }));

  return (
    <View
      onLayout={(e: LayoutChangeEvent) => setLargura(e.nativeEvent.layout.width - 8)}
      style={{ flexDirection: 'row', padding: 4, borderRadius: raios.md, backgroundColor: escuro ? cores.superficie2 : cores.superficie3 }}
      accessibilityRole="tablist">
      {largura > 0 ? (
        <Animated.View
          style={[
            { position: 'absolute', top: 4, bottom: 4, left: 4, borderRadius: raios.sm, backgroundColor: escuro ? cores.superficie3 : cores.superficie, boxShadow: escuro ? undefined : '0px 1px 3px rgba(0,0,0,0.12)' },
            indicador,
          ]}
        />
      ) : null}
      {opcoes.map((o) => {
        const ativo = o.valor === valor;
        return (
          <Pressionavel
            key={o.valor}
            escala={0.95}
            vibrar
            accessibilityRole="tab"
            accessibilityState={{ selected: ativo }}
            onPress={() => aoMudar(o.valor)}
            style={{ flex: 1, height: 36, alignItems: 'center', justifyContent: 'center' }}>
            <Texto variante="legenda" tom={ativo ? 'padrao' : 'fraco'} style={{ fontFamily: ativo ? fontes.negrito : fontes.seminegrito }}>
              {o.rotulo}
            </Texto>
          </Pressionavel>
        );
      })}
    </View>
  );
}
