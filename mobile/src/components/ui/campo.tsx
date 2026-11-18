/**
 * Entrada de texto do app.
 *
 * Feito para o iPhone não "quebrar" com a Montserrat: o campo de uma linha tem
 * ALTURA FIXA e o TextInput não tem padding vertical nem lineHeight — é a
 * combinação que deixava o texto desalinhado/cortado no iOS. Ícone e olho da
 * senha têm área própria (44 pt de toque). Foco = borda laranja mais forte;
 * erro = borda vermelha + mensagem embaixo.
 */
import { type ComponentRef, forwardRef, useState } from 'react';
import { Platform, Pressable, TextInput, type TextInputProps, View } from 'react-native';

import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

import { Icone, type NomeIcone } from './icone';
import { Texto } from './texto';

export type CampoProps = TextInputProps & {
  rotulo?: string;
  erro?: string | null;
  icone?: NomeIcone;
  ajuda?: string;
  /** Campo de senha com o olho para mostrar/esconder. */
  senha?: boolean;
  /** "suave": cinza claro e bem arredondado, como os campos do login do portal. */
  variante?: 'padrao' | 'suave';
};

export const Campo = forwardRef<ComponentRef<typeof TextInput>, CampoProps>(function Campo(
  { rotulo, erro, icone, ajuda, senha, variante = 'padrao', multiline, style, onFocus, onBlur, editable, ...resto },
  ref,
) {
  const cores = useCores();
  const [focado, setFocado] = useState(false);
  const [ver, setVer] = useState(false);
  const suave = variante === 'suave';
  const altura = suave ? 56 : 52;
  const corBorda = erro ? estado.perigo : focado ? marca[500] : cores.borda;

  return (
    <View style={{ gap: 8 }}>
      {rotulo ? (
        <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito, fontSize: 14 }}>
          {rotulo}
        </Texto>
      ) : null}
      <View
        style={{
          flexDirection: 'row',
          alignItems: multiline ? 'flex-start' : 'center',
          height: multiline ? undefined : altura,
          minHeight: multiline ? 128 : undefined,
          paddingLeft: icone ? 4 : 16,
          paddingRight: senha ? 4 : 16,
          borderWidth: focado || erro ? 1.5 : 1,
          borderColor: corBorda,
          borderRadius: suave ? raios.lg : raios.md,
          backgroundColor: focado && !erro ? comAlfa(marca[500], 0.04) : suave ? cores.superficie2 : cores.superficie,
          opacity: editable === false ? 0.6 : 1,
        }}>
        {icone ? (
          <View style={{ width: 44, height: multiline ? 48 : '100%', alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={icone} tamanho={20} cor={erro ? estado.perigo : focado ? marca[500] : cores.texto3} />
          </View>
        ) : null}
        <TextInput
          ref={ref}
          placeholderTextColor={cores.texto3}
          selectionColor={marca[500]}
          cursorColor={marca[500]}
          secureTextEntry={senha && !ver}
          multiline={multiline}
          editable={editable}
          underlineColorAndroid="transparent"
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
              fontSize: 16,
              ...(multiline
                ? { paddingTop: 14, paddingBottom: 14, textAlignVertical: 'top' as const, minHeight: 128 }
                : { height: '100%' as const, paddingVertical: 0 }),
              ...(Platform.OS === 'android' ? { includeFontPadding: false } : null),
            },
            // No navegador, o contorno de foco é a borda laranja do próprio campo.
            Platform.OS === 'web' ? ({ outlineWidth: 0 } as object) : null,
            style,
          ]}
          {...resto}
        />
        {senha ? (
          <Pressable
            onPress={() => setVer((v) => !v)}
            accessibilityRole="button"
            accessibilityLabel={ver ? 'Esconder senha' : 'Mostrar senha'}
            style={{ width: 44, height: '100%', alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={ver ? 'eye-off-outline' : 'eye-outline'} tamanho={21} cor={cores.texto3} />
          </Pressable>
        ) : null}
      </View>
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
