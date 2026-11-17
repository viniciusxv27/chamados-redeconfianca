import { type Href, router } from 'expo-router';
import { View } from 'react-native';

import { Avatar, Cartao, Icone, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { CardMeta } from '@/services/modulos/impulso';

import { BarraProgresso } from './barra';
import { COR_PRIORIDADE_META, COR_STATUS_META, dataCurta, textoDoPrazo } from './comum';

/** Uma meta no Kanban: faixa da cor do status, título, de quem, prazo, to-do e novidades. */
export function CartaoMeta({ meta, mostrarDono }: { meta: CardMeta; mostrarDono: boolean }) {
  const cores = useCores();
  const concluida = meta.status === 'CONCLUIDA';
  const prazo = textoDoPrazo(meta.dias_para_o_prazo, concluida);
  const corPrazo = meta.atrasado ? estado.perigo : meta.dias_para_o_prazo === 0 ? estado.alerta : cores.texto3;
  const dono = meta.colaborador;

  return (
    <Cartao
      onPress={() => router.push({ pathname: '/modulo/impulso/[id]', params: { id: String(meta.id) } } as unknown as Href)}
      acessibilidade={`${meta.titulo}, ${meta.status_nome}${prazo ? `, ${prazo}` : ''}`}
      style={{ overflow: 'hidden' }}>
      <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, backgroundColor: COR_STATUS_META[meta.status] }} />
      <View className="gap-3 p-4 pl-5">
        <View className="flex-row items-start gap-3">
          <Texto variante="corpoForte" numberOfLines={2} className="flex-1">
            {meta.titulo}
          </Texto>
          {meta.tem_novidade ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: marca[500] }}>
              <Icone nome="sparkles" tamanho={11} cor="#fff" />
              <Texto variante="legenda" tom="branco" style={{ fontSize: 11, fontFamily: fontes.negrito }}>
                Novo
              </Texto>
            </View>
          ) : null}
        </View>

        <View className="flex-row flex-wrap items-center gap-2">
          {meta.prioridade === 'ALTA' || meta.prioridade === 'CRITICA' ? (
            <Selo texto={meta.prioridade_nome} cor={COR_PRIORIDADE_META[meta.prioridade]} ponto={false} />
          ) : null}
          {meta.em_parceria ? <Selo texto="Em parceria" cor={estado.info} ponto={false} /> : null}
          {meta.recorrente ? <Selo texto="Recorrente" cor="#8B5CF6" ponto={false} /> : null}
          {concluida && meta.nota != null ? <Selo texto={`Nota ${meta.nota.toLocaleString('pt-BR')}`} cor={estado.sucesso} ponto={false} /> : null}
        </View>

        {meta.total_itens > 0 ? (
          <View className="gap-1.5">
            <View className="flex-row items-center justify-between">
              <Texto variante="legenda" tom="fraco">
                To-do
              </Texto>
              <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                {meta.feitos}/{meta.total_itens}
              </Texto>
            </View>
            <BarraProgresso pct={(meta.feitos / meta.total_itens) * 100} cor={COR_STATUS_META[meta.status]} altura={6} />
          </View>
        ) : null}

        <View className="flex-row items-center justify-between gap-3">
          {mostrarDono && dono ? (
            <View className="flex-1 flex-row items-center gap-2">
              <Avatar nome={dono.nome} foto={dono.foto} tamanho={22} />
              <Texto variante="legenda" tom="suave" numberOfLines={1} className="flex-1">
                {dono.nome}
              </Texto>
            </View>
          ) : (
            <View className="flex-1 flex-row items-center gap-1.5">
              <Icone nome="calendar-outline" tamanho={14} cor={cores.texto3} />
              <Texto variante="legenda" tom="fraco">
                {meta.prazo ? dataCurta(meta.prazo) : 'Sem prazo'}
              </Texto>
            </View>
          )}
          {prazo ? (
            <View className="flex-row items-center gap-1">
              <Icone nome={meta.atrasado ? 'alarm' : 'time-outline'} tamanho={14} cor={corPrazo} />
              <Texto variante="legenda" style={{ color: corPrazo, fontFamily: meta.atrasado ? fontes.seminegrito : fontes.media }}>
                {prazo}
              </Texto>
            </View>
          ) : null}
        </View>
      </View>
    </Cartao>
  );
}
