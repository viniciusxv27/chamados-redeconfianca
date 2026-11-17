import { type ComponentRef, forwardRef, useState } from 'react';
import { Platform, TextInput, type TextInputProps, View } from 'react-native';
import Animated, { useAnimatedStyle, useDerivedValue, withTiming, interpolateColor } from 'react-native-reanimated';

import { estado, fontes, marca, movimento, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';

import { Icone, type NomeIcone } from './icone';
import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

export type CampoProps = TextInputProps & {
  rotulo?: string;
  erro?: string | null;
  icone?: NomeIcone;
  ajuda?: string;
  /** Campo de senha com o olho para mostrar/esconder. */
  senha?: boolean;
};

/** Entrada de texto: borda acende em laranja no foco e fica vermelha no erro. */
export const Campo = forwardRef<ComponentRef<typeof TextInput>, CampoProps>(function Campo(
  { rotulo, erro, icone, ajuda, senha, multiline, style, onFocus, onBlur, ...resto },
  ref,
) {
  const cores = useCores();
  const [focado, setFocado] = useState(false);
  const [ver, setVer] = useState(false);
  const foco = useDerivedValue(() => withTiming(focado ? 1 : 0, { duration: movimento.rapido }));
  const borda = useAnimatedStyle(() => ({
    borderColor: erro ? estado.perigo : interpolateColor(foco.get(), [0, 1], [cores.borda, marca[500]]),
    boxShadow: focado && !erro ? `0px 0px 0px 3px ${marca[500]}22` : undefined,
  }));

  return (
    <View className="gap-1.5">
      {rotulo ? (
        <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
          {rotulo}
        </Texto>
      ) : null}
      <Animated.View
        style={[
          {
            flexDirection: 'row',
            alignItems: multiline ? 'flex-start' : 'center',
            gap: 10,
            minHeight: 50,
            paddingHorizontal: 14,
            borderWidth: 1.5,
            borderRadius: raios.md,
            backgroundColor: cores.superficie,
          },
          borda,
        ]}>
        {icone ? <Icone nome={icone} tamanho={19} cor={focado ? marca[500] : cores.texto3} style={multiline ? { marginTop: 14 } : undefined} /> : null}
        <TextInput
          ref={ref}
          placeholderTextColor={cores.texto3}
          selectionColor={marca[500]}
          secureTextEntry={senha && !ver}
          multiline={multiline}
          onFocus={(e) => {
            setFocado(true);
            onFocus?.(e);
          }}
          onBlur={(e) => {
            setFocado(false);
            onBlur?.(e);
          }}
          style={[
            {
              flex: 1,
              color: cores.texto,
              fontFamily: fontes.media,
              fontSize: 15,
              paddingVertical: multiline ? 14 : 12,
              minHeight: multiline ? 120 : undefined,
              textAlignVertical: multiline ? 'top' : 'center',
            },
            // No navegador, o contorno de foco é a borda laranja do próprio campo.
            Platform.OS === 'web' ? ({ outlineWidth: 0 } as object) : null,
            style,
          ]}
          {...resto}
        />
        {senha ? (
          <Pressionavel onPress={() => setVer((v) => !v)} hitSlop={10} accessibilityLabel={ver ? 'Esconder senha' : 'Mostrar senha'}>
            <Icone nome={ver ? 'eye-off-outline' : 'eye-outline'} tamanho={20} cor={cores.texto3} />
          </Pressionavel>
        ) : null}
      </Animated.View>
      {erro ? (
        <Texto variante="legenda" tom="perigo">
          {erro}
        </Texto>
      ) : ajuda ? (
        <Texto variante="legenda" tom="fraco">
          {ajuda}
        </Texto>
      ) : null}
    </View>
  );
});
