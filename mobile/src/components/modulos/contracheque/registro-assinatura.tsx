/** Comprovante da assinatura já feita (data, IP, hash e o desenho), como o quadro verde da web. */
import { Image } from 'expo-image';
import { View } from 'react-native';

import { Botao, Icone, Texto } from '@/components/ui';
import { estado, raios } from '@/constants/theme';
import { comAlfa, dataHora } from '@/utils/formatar';

import { LinhaValor } from '../comum';

export function RegistroAssinatura({
  titulo,
  em,
  ip,
  hash,
  imagem,
  aoAbrirAssinado,
  abrindo,
}: {
  titulo: string;
  em: string | null;
  ip: string | null;
  hash: string | null;
  imagem: string | null;
  aoAbrirAssinado?: () => void;
  abrindo?: boolean;
}) {
  return (
    <View className="gap-2">
      <View className="flex-row items-center gap-2">
        <View style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: comAlfa(estado.sucesso, 0.14), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome="ribbon-outline" tamanho={18} cor={estado.sucesso} />
        </View>
        <Texto variante="corpoForte" style={{ color: estado.sucesso }} className="flex-1">
          {titulo}
        </Texto>
      </View>
      <View>
        <LinhaValor rotulo="Assinado em" valor={dataHora(em) || '—'} />
        <LinhaValor rotulo="IP" valor={ip || '—'} />
        {hash ? <LinhaValor rotulo="Hash" valor={hash} /> : null}
      </View>
      {imagem ? (
        <View style={{ backgroundColor: '#FFFFFF', borderRadius: raios.sm, padding: 8, borderWidth: 1, borderColor: comAlfa(estado.sucesso, 0.3) }}>
          <Image source={{ uri: imagem }} contentFit="contain" style={{ width: '100%', height: 72 }} accessibilityLabel="Sua assinatura" />
        </View>
      ) : null}
      {aoAbrirAssinado ? (
        <Botao titulo="Abrir PDF assinado" icone="document-attach-outline" variante="secundario" carregando={abrindo} onPress={aoAbrirAssinado} style={{ marginTop: 6 }} />
      ) : null}
    </View>
  );
}
