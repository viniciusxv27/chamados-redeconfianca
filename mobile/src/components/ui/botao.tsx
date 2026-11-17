import * as Haptics from 'expo-haptics';
import { ActivityIndicator, Platform, View, type StyleProp, type ViewStyle } from 'react-native';

import { estado, marca, raios, sombras } from '@/constants/theme';
import { useCores } from '@/providers/tema';

import { Icone, type NomeIcone } from './icone';
import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

type Variante = 'primario' | 'secundario' | 'fantasma' | 'perigo';
type Tamanho = 'md' | 'lg' | 'sm';

const ALTURA: Record<Tamanho, number> = { sm: 38, md: 48, lg: 56 };

export function Botao({
  titulo,
  onPress,
  variante = 'primario',
  tamanho = 'md',
  icone,
  carregando = false,
  desativado = false,
  larguraTotal = true,
  style,
}: {
  titulo: string;
  onPress?: () => void;
  variante?: Variante;
  tamanho?: Tamanho;
  icone?: NomeIcone;
  carregando?: boolean;
  desativado?: boolean;
  larguraTotal?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const cores = useCores();
  const fundo = { primario: marca[500], secundario: cores.superficie2, fantasma: 'transparent', perigo: estado.perigo }[variante];
  const tinta = { primario: '#FFFFFF', secundario: cores.texto, fantasma: marca[500], perigo: '#FFFFFF' }[variante];
  const borda = variante === 'secundario' ? cores.borda : 'transparent';

  return (
    <Pressionavel
      accessibilityLabel={titulo}
      accessibilityState={{ disabled: desativado || carregando, busy: carregando }}
      disabled={desativado || carregando}
      onPress={() => {
        if (Platform.OS !== 'web') void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        onPress?.();
      }}
      style={[
        {
          height: ALTURA[tamanho],
          paddingHorizontal: tamanho === 'sm' ? 12 : 16,
          borderRadius: raios.md,
          backgroundColor: fundo,
          borderWidth: 1,
          borderColor: borda,
          alignSelf: larguraTotal ? 'stretch' : 'flex-start',
          boxShadow: variante === 'primario' && !desativado ? sombras.flutuante : undefined,
        },
        style,
      ]}>
      <View className="flex-1 flex-row items-center justify-center gap-2">
        {carregando ? (
          <ActivityIndicator color={tinta} />
        ) : (
          <>
            {icone ? <Icone nome={icone} tamanho={tamanho === 'sm' ? 16 : 19} cor={tinta} /> : null}
            <Texto variante="corpoForte" numberOfLines={1} style={{ color: tinta, fontSize: tamanho === 'sm' ? 14 : 15, flexShrink: 1 }}>
              {titulo}
            </Texto>
          </>
        )}
      </View>
    </Pressionavel>
  );
}
