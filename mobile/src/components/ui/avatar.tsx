import { Image } from 'expo-image';
import { View } from 'react-native';

import { fontes } from '@/constants/theme';
import { corDoNome, iniciais } from '@/utils/formatar';

import { Texto } from './texto';

/** Foto da pessoa ou as iniciais numa cor fixa do nome. */
export function Avatar({ nome, foto, tamanho = 40, borda }: { nome: string; foto?: string | null; tamanho?: number; borda?: string }) {
  const estilo = { width: tamanho, height: tamanho, borderRadius: tamanho / 2, borderWidth: borda ? 2 : 0, borderColor: borda };
  if (foto) {
    return <Image source={{ uri: foto }} style={estilo} contentFit="cover" transition={200} accessibilityLabel={`Foto de ${nome}`} />;
  }
  return (
    <View style={[estilo, { backgroundColor: corDoNome(nome), alignItems: 'center', justifyContent: 'center' }]}>
      <Texto style={{ color: '#fff', fontFamily: fontes.negrito, fontSize: tamanho * 0.38 }}>{iniciais(nome)}</Texto>
    </View>
  );
}
