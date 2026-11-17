/**
 * Moldura das telas de detalhe com assinatura (Contracheque e Documentos):
 * barra com voltar, rolagem com puxar-para-atualizar que trava enquanto a
 * pessoa desenha a assinatura, e os estados de carregando/erro.
 */
import type { ReactNode } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { BarraDetalhe, BotaoIcone, Cartao, EstadoErro, Icone, type NomeIcone, Tela, Texto } from '@/components/ui';
import { marca } from '@/constants/theme';
import { mensagemDoErro } from '@/services/api';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

import { EsqueletoBloco } from '../comum';

export function TelaDetalhe({
  titulo,
  caminhoWeb,
  carregando,
  erro,
  aoAtualizar,
  atualizando = false,
  rolagemTravada = false,
  children,
}: {
  titulo: string;
  caminhoWeb?: string;
  carregando: boolean;
  erro: unknown;
  aoAtualizar: () => void;
  atualizando?: boolean;
  rolagemTravada?: boolean;
  children?: ReactNode;
}) {
  return (
    <Tela>
      <BarraDetalhe
        titulo={titulo}
        direita={caminhoWeb ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(caminhoWeb)} /> : undefined}
      />
      <ScrollView
        scrollEnabled={!rolagemTravada}
        contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 48 }}
        refreshControl={<RefreshControl refreshing={atualizando} onRefresh={aoAtualizar} tintColor={marca[500]} colors={[marca[500]]} />}>
        {carregando ? (
          <View className="gap-4">
            <EsqueletoBloco linhas={3} />
            <EsqueletoBloco linhas={4} />
            <EsqueletoBloco linhas={2} />
          </View>
        ) : erro ? (
          <EstadoErro mensagem={mensagemDoErro(erro)} aoTentar={aoAtualizar} />
        ) : (
          children
        )}
      </ScrollView>
    </Tela>
  );
}

/** Cartão com título pequeno (ícone + rótulo), entrando em cascata. */
export function Secao({ icone, titulo, cor, indice = 0, children }: { icone: NomeIcone; titulo: string; cor?: string; indice?: number; children: ReactNode }) {
  const cores = useCores();
  return (
    <Animated.View entering={FadeInDown.delay(Math.min(indice, 6) * 60).duration(320)}>
      <Cartao style={{ padding: 16, gap: 8, borderColor: cor ? comAlfa(cor, 0.35) : undefined }}>
        <View className="flex-row items-center gap-2">
          <Icone nome={icone} tamanho={18} cor={cor ?? cores.texto2} />
          <Texto variante="rotulo" tom="suave">
            {titulo}
          </Texto>
        </View>
        {children}
      </Cartao>
    </Animated.View>
  );
}
