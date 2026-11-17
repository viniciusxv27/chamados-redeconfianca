/** Cartão de um documento na lista: categoria, situação, título e data (como o card da web). */
import { View } from 'react-native';

import { Cartao, Icone, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { DocumentoResumo } from '@/services/modulos/documentos';
import { tempoRelativo } from '@/utils/formatar';

export function CartaoDocumento({ documento: d, aoAbrir }: { documento: DocumentoResumo; aoAbrir: () => void }) {
  const cores = useCores();
  const cor = d.assinado ? estado.sucesso : estado.alerta;
  return (
    <Cartao acessibilidade={`${d.titulo}, ${d.assinado ? 'assinado' : 'pendente'}`} onPress={aoAbrir} style={{ padding: 16, gap: 8, borderLeftWidth: 4, borderLeftColor: cor }}>
      <View className="flex-row items-center justify-between gap-3">
        <Texto variante="rotulo" tom="fraco" numberOfLines={1} className="flex-1">
          {d.categoria?.nome ?? 'Documento'}
        </Texto>
        <Selo texto={d.assinado ? 'Assinado' : 'Pendente'} cor={cor} />
      </View>
      <Texto variante="corpoForte">{d.titulo}</Texto>
      {d.descricao ? (
        <Texto variante="legenda" tom="suave" numberOfLines={2}>
          {d.descricao}
        </Texto>
      ) : null}
      <View className="flex-row items-center justify-between gap-3">
        <View className="flex-row items-center gap-1">
          <Icone nome={d.assinado ? 'checkmark-done-outline' : 'time-outline'} tamanho={14} cor={cores.texto3} />
          <Texto variante="legenda" tom="fraco">
            {d.assinado ? `Assinado ${tempoRelativo(d.assinado_em)}` : `Recebido ${tempoRelativo(d.atribuido_em)}`}
          </Texto>
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
    </Cartao>
  );
}
