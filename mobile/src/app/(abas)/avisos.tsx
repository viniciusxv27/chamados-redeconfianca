import { FlatList, RefreshControl, View } from 'react-native';
import Animated, { LinearTransition } from 'react-native-reanimated';

import { CabecalhoGrande, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, type NomeIcone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, marca, raios } from '@/constants/theme';
import { useMarcarLida, useMarcarTodas, useNotificacoes } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import type { Notificacao } from '@/services/tipos';
import { useTema } from '@/providers/tema';
import { comAlfa, tempoRelativo } from '@/utils/formatar';
import { abrirLink } from '@/utils/links';

const ICONES: Record<string, [NomeIcone, string]> = {
  TICKET: ['chatbubbles-outline', marca[500]],
  TICKET_UPDATE: ['chatbubbles-outline', marca[500]],
  COMMUNICATION: ['megaphone-outline', estado.info],
  TRAINING: ['school-outline', estado.sucesso],
  FILE: ['document-outline', estado.secundaria],
};

/** Avisos: junta o sino do portal com os avisos antigos (cursos, documentos, caixa). */
export default function Avisos() {
  const { data, isPending, isError, error, refetch, isRefetching } = useNotificacoes();
  const marcarTodas = useMarcarTodas();
  const naoLidas = data?.nao_lidas ?? 0;

  return (
    <Tela topo>
      <CabecalhoGrande
        titulo="Avisos"
        subtitulo={naoLidas ? `${naoLidas} não ${naoLidas === 1 ? 'lido' : 'lidos'}` : 'Tudo lido'}
        direita={
          naoLidas ? (
            <Pressionavel onPress={() => marcarTodas.mutate()} hitSlop={8} escala={0.94} accessibilityLabel="Marcar todos como lidos">
              <Texto variante="legenda" tom="marca" style={{ fontSize: 14 }}>
                Marcar todos
              </Texto>
            </Pressionavel>
          ) : undefined
        }
      />
      {isPending ? (
        <View className="px-5">
          <EsqueletoLista quantos={6} />
        </View>
      ) : isError && !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <FlatList
          data={data?.resultados ?? []}
          keyExtractor={(n) => n.id}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 10 ? index : 0}>
              <ItemAviso aviso={item} />
            </Entrada>
          )}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40, gap: 12 }}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          ListEmptyComponent={<EstadoVazio icone="notifications-off-outline" titulo="Nenhum aviso" texto="Quando algo acontecer nos seus chamados ou no portal, você fica sabendo aqui." />}
        />
      )}
    </Tela>
  );
}

function ItemAviso({ aviso }: { aviso: Notificacao }) {
  const { cores, escuro } = useTema();
  const marcar = useMarcarLida();
  const [icone, cor] = ICONES[aviso.tipo] ?? ['notifications-outline', cores.texto2];

  return (
    <Animated.View layout={LinearTransition}>
      <Pressionavel
        onPress={() => {
          if (!aviso.lida) marcar.mutate(aviso.id);
          abrirLink(aviso.link);
        }}
        escala={0.985}
        accessibilityLabel={`${aviso.lida ? '' : 'Não lido. '}${aviso.titulo}`}
        style={{
          flexDirection: 'row',
          gap: 12,
          padding: 14,
          borderRadius: raios.lg,
          backgroundColor: aviso.lida ? cores.superficie : escuro ? comAlfa(marca[500], 0.1) : marca[50],
          borderWidth: 1,
          borderColor: aviso.lida ? cores.bordaSuave : comAlfa(marca[500], 0.25),
        }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={icone} tamanho={20} cor={cor} />
        </View>
        <View className="flex-1 gap-0.5">
          <View className="flex-row items-start gap-2">
            <Texto variante={aviso.lida ? 'corpo' : 'corpoForte'} className="flex-1" numberOfLines={2}>
              {aviso.titulo}
            </Texto>
            <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
              {tempoRelativo(aviso.criada_em)}
            </Texto>
          </View>
          {aviso.mensagem ? (
            <Texto variante="legenda" tom="suave" numberOfLines={3}>
              {aviso.mensagem}
            </Texto>
          ) : null}
        </View>
        {!aviso.lida ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: marca[500], marginTop: 6 }} /> : null}
      </Pressionavel>
    </Animated.View>
  );
}
