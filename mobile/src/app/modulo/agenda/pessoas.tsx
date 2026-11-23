/** Escolher alguém para ver a agenda (completa ou só os horários ocupados) e pedir uma reunião. */
import { type Href, router } from 'expo-router';
import { useMemo, useState } from 'react';
import { FlatList, View } from 'react-native';

import { EsqueletoBloco } from '@/components/modulos/comum';
import { Avatar, BarraDetalhe, CabecalhoGrande, Campo, EstadoErro, Icone, Pressionavel, Tela, Texto } from '@/components/ui';
import { raios } from '@/constants/theme';
import { useOpcoesAgenda } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';

function semAcento(t: string) {
  return t
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

export default function PessoasDaAgenda() {
  const cores = useCores();
  const { data, isPending, error, refetch } = useOpcoesAgenda();
  const [busca, setBusca] = useState('');
  const pessoas = useMemo(() => {
    const termo = semAcento(busca.trim());
    const todas = (data?.pessoas ?? []).filter((p) => p.id !== data?.eu);
    return termo ? todas.filter((p) => semAcento(`${p.nome} ${p.setor ?? ''}`).includes(termo)) : todas;
  }, [data, busca]);

  return (
    <Tela>
      <BarraDetalhe />
      <CabecalhoGrande titulo="Agenda de alguém" subtitulo="Veja os horários livres e peça uma reunião" />
      <View style={{ paddingHorizontal: 20, paddingBottom: 8 }}>
        <Campo icone="search" placeholder="Buscar pessoa ou setor…" value={busca} onChangeText={setBusca} autoCorrect={false} />
      </View>
      {isPending ? (
        <View style={{ paddingHorizontal: 20 }}>
          <EsqueletoBloco linhas={6} />
        </View>
      ) : error ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <FlatList
          data={pessoas}
          keyExtractor={(p) => String(p.id)}
          keyboardShouldPersistTaps="handled"
          initialNumToRender={20}
          contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 40 }}
          renderItem={({ item }) => (
            <Pressionavel
              onPress={() => router.push({ pathname: '/modulo/agenda/pessoa/[id]', params: { id: String(item.id) } } as unknown as Href)}
              escala={0.98}
              accessibilityLabel={`Agenda de ${item.nome}`}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, paddingVertical: 10, borderRadius: raios.md }}>
              <Avatar nome={item.nome} tamanho={38} />
              <View className="flex-1">
                <Texto variante="corpo" numberOfLines={1}>
                  {item.nome}
                </Texto>
                {item.setor ? (
                  <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                    {item.setor}
                  </Texto>
                ) : null}
              </View>
              <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
            </Pressionavel>
          )}
          ListEmptyComponent={
            <Texto tom="fraco" className="py-8 text-center">
              Ninguém encontrado.
            </Texto>
          }
        />
      )}
    </Tela>
  );
}
