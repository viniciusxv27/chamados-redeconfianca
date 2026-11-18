import { type Href, router } from 'expo-router';
import { RefreshControl, ScrollView, View } from 'react-native';

import { CabecalhoGrande, Cartao, Entrada, Esqueleto, EstadoErro, Icone, type NomeIcone, Tela, Texto } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useModulos } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { comAlfa } from '@/utils/formatar';

/**
 * Módulos do portal. A lista vem do servidor já filtrada pela regra de acesso
 * de cada módulo (a mesma do menu do portal); o Mural fica sempre.
 */
export default function Modulos() {
  const { data, isPending, isError, error, refetch, isRefetching } = useModulos();

  const itens = [
    ...(data ?? []),
    { chave: 'mural', rotulo: 'Mural', descricao: 'Comunicados da rede', icone: 'megaphone-outline', cor: '#EF4444', rota: '/comunicados' },
  ];

  return (
    <Tela topo>
      <CabecalhoGrande titulo="Módulos" subtitulo="Portal" />
      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}>
        {isPending ? (
          <View className="flex-row flex-wrap gap-3">
            {Array.from({ length: 6 }, (_, i) => (
              <Cartao key={i} style={{ flexBasis: '47%', flexGrow: 1, padding: 16, gap: 12 }}>
                <Esqueleto largura={44} altura={44} raio={14} />
                <Esqueleto largura="70%" />
                <Esqueleto largura="90%" altura={11} />
              </Cartao>
            ))}
          </View>
        ) : isError && !data ? (
          <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
        ) : (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 14 }}>
            {itens.map((m, i) => (
              <Entrada key={m.chave} indice={i} style={{ flexBasis: '47%', flexGrow: 1 }}>
                <Cartao onPress={() => router.push(m.rota as Href)} acessibilidade={`Abrir ${m.rotulo}`} style={{ padding: 18, gap: 14, minHeight: 140 }}>
                  <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: comAlfa(m.cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
                    <Icone nome={m.icone as NomeIcone} tamanho={22} cor={m.cor} />
                  </View>
                  <View className="gap-0.5">
                    <Texto variante="corpoForte">{m.rotulo}</Texto>
                    {m.descricao ? (
                      <Texto variante="legenda" tom="fraco" numberOfLines={2}>
                        {m.descricao}
                      </Texto>
                    ) : null}
                  </View>
                </Cartao>
              </Entrada>
            ))}
          </View>
        )}
      </ScrollView>
    </Tela>
  );
}
