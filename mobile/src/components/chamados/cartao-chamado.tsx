import { router } from 'expo-router';
import { View } from 'react-native';

import { Avatar, Cartao, Icone, SeloPrioridade, SeloStatus, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { ChamadoResumo } from '@/services/tipos';
import { COR_STATUS, prazo, tempoRelativo } from '@/utils/formatar';

/** Um chamado na lista: faixa de cor do status à esquerda, título, quem, quando. */
export function CartaoChamado({ chamado }: { chamado: ChamadoResumo }) {
  const cores = useCores();
  const pessoa = chamado.responsavel ?? chamado.solicitante;
  const textoPrazo = chamado.em_aberto ? prazo(chamado.prazo) : null;

  return (
    <Cartao
      onPress={() => router.push({ pathname: '/chamado/[id]', params: { id: String(chamado.id) } })}
      acessibilidade={`Chamado ${chamado.id}: ${chamado.titulo}, ${chamado.status_nome}`}
      style={{ overflow: 'hidden' }}>
      <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, backgroundColor: COR_STATUS[chamado.status] }} />
      <View className="gap-3 p-4 pl-5">
        <View className="flex-row items-start gap-3">
          <View className="flex-1 gap-1">
            <Texto variante="legenda" tom="fraco">
              #{chamado.id} · {chamado.setor ?? 'Sem setor'}
              {chamado.categoria ? ` · ${chamado.categoria}` : ''}
            </Texto>
            <Texto variante="corpoForte" numberOfLines={2}>
              {chamado.titulo}
            </Texto>
          </View>
          <Texto variante="legenda" tom="fraco">
            {tempoRelativo(chamado.atualizado_em)}
          </Texto>
        </View>

        <View className="flex-row flex-wrap items-center gap-2">
          <SeloStatus status={chamado.status} nome={chamado.status_nome} />
          {chamado.prioridade === 'ALTA' || chamado.prioridade === 'CRITICA' ? (
            <SeloPrioridade prioridade={chamado.prioridade} nome={chamado.prioridade_nome} />
          ) : null}
        </View>

        <View className="flex-row items-center justify-between gap-3">
          {pessoa ? (
            <View className="flex-1 flex-row items-center gap-2">
              <Avatar nome={pessoa.nome} foto={pessoa.foto} tamanho={22} />
              <Texto variante="legenda" tom="suave" numberOfLines={1} className="flex-1">
                {chamado.responsavel ? pessoa.nome : `Aberto por ${pessoa.primeiro_nome}`}
              </Texto>
            </View>
          ) : (
            <Texto variante="legenda" tom="fraco" className="flex-1">
              Sem responsável ainda
            </Texto>
          )}
          {textoPrazo ? (
            <View className="flex-row items-center gap-1">
              <Icone nome={chamado.atrasado ? 'alarm' : 'time-outline'} tamanho={14} cor={chamado.atrasado ? estado.perigo : cores.texto3} />
              <Texto variante="legenda" style={{ color: chamado.atrasado ? estado.perigo : cores.texto3, fontFamily: chamado.atrasado ? fontes.seminegrito : fontes.media }}>
                {textoPrazo}
              </Texto>
            </View>
          ) : null}
        </View>
      </View>
    </Cartao>
  );
}
