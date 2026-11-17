import { useState } from 'react';
import { View } from 'react-native';
import Animated, { FadeInDown, ZoomIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Botao, Icone, type NomeIcone, Tela, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { mensagemDoErro } from '@/services/api';
import { useSessao } from '@/providers/sessao';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const ICONES: Record<string, [NomeIcone, string]> = {
  ferias: ['sunny', estado.alerta],
  jornada: ['time', marca[500]],
  curso: ['school', estado.info],
  trilha: ['map', estado.info],
  cadastro: ['id-card', estado.perigo],
};

/**
 * O portal bloqueou a pessoa (férias, jornada, curso, trilha, cadastro). O app
 * respeita a mesma regra: explica o motivo, leva ao portal para resolver e
 * confere de novo quando ela voltar.
 */
export default function Bloqueado() {
  const { bloqueio, conferir, sair } = useSessao();
  const insets = useSafeAreaInsets();
  const [conferindo, setConferindo] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [icone, cor] = ICONES[bloqueio?.tipo ?? ''] ?? ['lock-closed', marca[500]];

  async function verificar() {
    setConferindo(true);
    setAviso(null);
    try {
      await conferir();
      setAviso('Ainda há uma pendência. Resolva no portal e tente de novo.');
    } catch (e) {
      setAviso(mensagemDoErro(e));
    } finally {
      setConferindo(false);
    }
  }

  return (
    <Tela style={{ paddingTop: insets.top, paddingBottom: insets.bottom + 16 }}>
      <View className="flex-1 items-center justify-center gap-5 px-8">
        <Animated.View entering={ZoomIn.springify().damping(14)} style={{ width: 112, height: 112, borderRadius: 56, backgroundColor: comAlfa(cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={icone} tamanho={52} cor={cor} />
        </Animated.View>
        <Animated.View entering={FadeInDown.delay(120)} className="items-center gap-2">
          <Texto variante="titulo1" className="text-center">
            {bloqueio?.titulo ?? 'Acesso bloqueado'}
          </Texto>
          <Texto tom="suave" className="text-center">
            {bloqueio?.mensagem}
          </Texto>
        </Animated.View>
        {aviso ? (
          <Texto variante="legenda" tom="fraco" className="text-center">
            {aviso}
          </Texto>
        ) : null}
      </View>
      <Animated.View entering={FadeInDown.delay(220)} className="gap-3 px-6">
        {bloqueio?.tipo !== 'ferias' ? <Botao titulo="Resolver no portal" icone="open-outline" tamanho="lg" onPress={() => abrirNoPortal(bloqueio?.caminho_web ?? '/')} /> : null}
        <Botao titulo="Já resolvi, verificar de novo" variante="secundario" icone="refresh" onPress={verificar} carregando={conferindo} />
        <Botao titulo="Sair" variante="fantasma" onPress={() => void sair()} />
      </Animated.View>
    </Tela>
  );
}
