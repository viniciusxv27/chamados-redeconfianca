import * as Haptics from 'expo-haptics';
import { type Href, router } from 'expo-router';
import { Platform, View } from 'react-native';

import { Botao, Cartao, Icone, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useResponderConvite } from '@/hooks/modulos/impulso';
import { mensagemDoErro } from '@/services/api';
import type { Convite } from '@/services/modulos/impulso';
import { comAlfa } from '@/utils/formatar';

import { dataCurta } from './comum';

/** Convite para tocar a meta de um colega junto: aceitar coloca a pessoa na meta. */
export function CartaoConvite({ convite }: { convite: Convite }) {
  const responder = useResponderConvite();
  const quem = convite.convidado_por?.primeiro_nome ?? convite.dono?.primeiro_nome ?? 'Um colega';

  function enviar(resposta: 'aceitar' | 'recusar') {
    responder.mutate(
      { id: convite.id, resposta },
      {
        onSuccess: () => {
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        },
      },
    );
  }

  return (
    <Cartao style={{ padding: 16, gap: 12, borderColor: comAlfa(estado.alerta, 0.45) }}>
      <View className="flex-row items-center gap-2">
        <Icone nome="people" tamanho={18} cor={estado.alerta} />
        <Texto variante="rotulo" tom="suave">
          Convite de parceria
        </Texto>
      </View>
      <Texto
        variante="corpoForte"
        onPress={() => router.push({ pathname: '/modulo/impulso/[id]', params: { id: String(convite.meta.id) } } as unknown as Href)}>
        {convite.meta.titulo}
      </Texto>
      <Texto variante="legenda" tom="suave">
        {quem} chamou você para tocar esta meta junto{convite.meta.prazo ? ` · prazo ${dataCurta(convite.meta.prazo)}` : ''}.
      </Texto>
      {convite.mensagem ? (
        <Texto variante="legenda" tom="fraco">
          “{convite.mensagem}”
        </Texto>
      ) : null}
      {responder.isError ? (
        <Texto variante="legenda" tom="perigo">
          {mensagemDoErro(responder.error)}
        </Texto>
      ) : null}
      <View className="flex-row gap-2">
        <Botao titulo="Aceitar" icone="checkmark" tamanho="sm" style={{ flex: 1 }} carregando={responder.isPending && responder.variables?.resposta === 'aceitar'} desativado={responder.isPending} onPress={() => enviar('aceitar')} />
        <Botao titulo="Recusar" variante="secundario" tamanho="sm" style={{ flex: 1 }} carregando={responder.isPending && responder.variables?.resposta === 'recusar'} desativado={responder.isPending} onPress={() => enviar('recusar')} />
      </View>
    </Cartao>
  );
}
