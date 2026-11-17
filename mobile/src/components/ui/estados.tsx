import { View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';

import { marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

import { Botao } from './botao';
import { Icone, type NomeIcone } from './icone';
import { Texto } from './texto';

function Bloco({ icone, cor, titulo, texto, acao, aoAgir }: { icone: NomeIcone; cor: string; titulo: string; texto?: string; acao?: string; aoAgir?: () => void }) {
  return (
    <Animated.View entering={FadeIn.duration(300)} style={{ alignItems: 'center', paddingVertical: 48, paddingHorizontal: 24, gap: 12 }}>
      <View style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: comAlfa(cor, 0.12), alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={icone} tamanho={32} cor={cor} />
      </View>
      <Texto variante="titulo3" className="text-center">
        {titulo}
      </Texto>
      {texto ? (
        <Texto tom="suave" className="text-center">
          {texto}
        </Texto>
      ) : null}
      {acao && aoAgir ? <Botao titulo={acao} onPress={aoAgir} variante="secundario" larguraTotal={false} icone="refresh" /> : null}
    </Animated.View>
  );
}

export function EstadoVazio({ icone = 'file-tray-outline', titulo, texto }: { icone?: NomeIcone; titulo: string; texto?: string }) {
  const cores = useCores();
  return <Bloco icone={icone} cor={cores.texto3} titulo={titulo} texto={texto} />;
}

export function EstadoErro({ mensagem, aoTentar }: { mensagem: string; aoTentar?: () => void }) {
  return <Bloco icone="cloud-offline-outline" cor={marca[500]} titulo="Não deu para carregar" texto={mensagem} acao="Tentar de novo" aoAgir={aoTentar} />;
}
