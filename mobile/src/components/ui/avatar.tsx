import { Image } from 'expo-image';
import { StyleSheet, View } from 'react-native';

import { fontes } from '@/constants/theme';
import { corDoNome, iniciais } from '@/utils/formatar';

import { Texto } from './texto';

/**
 * Foto da pessoa ou as iniciais numa cor fixa do nome. Com foto, as iniciais
 * ficam por baixo enquanto ela carrega (e se ela falhar) — nunca um buraco vazio.
 */
export function Avatar({ nome, foto, tamanho = 40, borda }: { nome: string; foto?: string | null; tamanho?: number; borda?: string }) {
  const redondo = { width: tamanho, height: tamanho, borderRadius: tamanho / 2 };
  return (
    <View style={[redondo, { borderWidth: borda ? 2 : 0, borderColor: borda, overflow: 'hidden' }]}>
      <View style={[StyleSheet.absoluteFill, { backgroundColor: corDoNome(nome), alignItems: 'center', justifyContent: 'center' }]}>
        <Texto style={{ color: '#fff', fontFamily: fontes.negrito, fontSize: tamanho * 0.38 }}>{iniciais(nome)}</Texto>
      </View>
      {foto ? (
        <Image source={{ uri: foto }} style={StyleSheet.absoluteFill} contentFit="cover" transition={250} cachePolicy="memory-disk" accessibilityLabel={`Foto de ${nome}`} />
      ) : null}
    </View>
  );
}
