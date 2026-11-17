import { useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { CartaoExecucao, diaPorExtenso, isoDoDia } from '@/components/modulos/checklists/pecas';
import { Avatar, Cartao, Entrada, Texto } from '@/components/ui';
import { useAcompanhamentoPessoa } from '@/hooks/modulos/checklists';

/** Os checklists de uma pessoa no dia e os atrasados dela (acompanhamento do gestor, só leitura). */
export default function ChecklistsDaPessoa() {
  const params = useLocalSearchParams<{ id: string; data?: string }>();
  const id = Number(params.id || 0);
  const dia = params.data || isoDoDia(new Date());
  const { data, isPending, isError, error, refetch, isRefetching } = useAcompanhamentoPessoa(id, dia);

  return (
    <TelaModulo
      titulo={data?.pessoa.nome ?? 'Checklists'}
      subtitulo={diaPorExtenso(dia, true)}
      carregando={isPending}
      erro={isError ? error : undefined}
      aoAtualizar={() => void refetch()}
      atualizando={isRefetching}>
      {data ? (
        <>
          <Entrada indice={0}>
            <Cartao style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <Avatar nome={data.pessoa.nome} foto={data.pessoa.foto} tamanho={44} />
              <View className="flex-1">
                <Texto variante="corpoForte" numberOfLines={1}>
                  {data.pessoa.nome}
                </Texto>
                <Texto variante="legenda" tom="suave" numberOfLines={1}>
                  {data.pessoa.setor ?? 'Sem setor'}
                </Texto>
              </View>
            </Cartao>
          </Entrada>

          <Texto variante="rotulo" tom="fraco">
            No dia
          </Texto>
          {data.do_dia.length ? (
            data.do_dia.map((e, i) => (
              <Entrada key={e.id} indice={i + 1}>
                <CartaoExecucao e={e} mostrarData={false} />
              </Entrada>
            ))
          ) : (
            <Texto variante="legenda" tom="suave">
              Nenhum checklist neste dia.
            </Texto>
          )}

          {data.atrasados.length ? (
            <>
              <Texto variante="rotulo" tom="fraco">
                Atrasados (2 dias ou mais)
              </Texto>
              {data.atrasados.map((e, i) => (
                <Entrada key={e.id} indice={i + 3}>
                  <CartaoExecucao e={e} />
                </Entrada>
              ))}
            </>
          ) : null}
        </>
      ) : null}
    </TelaModulo>
  );
}
