import { useMemo, useState } from 'react';
import { FlatList, Modal, Pressable, View } from 'react-native';
import Animated, { FadeIn, FadeOut, SlideInDown, SlideOutDown } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';

import { Campo } from './campo';
import { Icone } from './icone';
import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

type Item = { id: number; nome: string; detalhe?: string };

/**
 * Campo de escolha que abre uma folha de baixo para cima (padrão mobile, em
 * vez do <select> do navegador), com busca quando a lista é longa.
 */
export function Seletor({
  rotulo,
  placeholder,
  itens,
  valor,
  aoEscolher,
  erro,
  carregando,
  desativado,
}: {
  rotulo: string;
  placeholder: string;
  itens: Item[];
  valor: number | null;
  aoEscolher: (id: number) => void;
  erro?: string | null;
  carregando?: boolean;
  desativado?: boolean;
}) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const [aberto, setAberto] = useState(false);
  const [busca, setBusca] = useState('');
  const escolhido = itens.find((i) => i.id === valor);
  const filtrados = useMemo(() => {
    const termo = busca
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase();
    if (!termo) return itens;
    return itens.filter((i) =>
      i.nome
        .normalize('NFD')
        .replace(/[̀-ͯ]/g, '')
        .toLowerCase()
        .includes(termo),
    );
  }, [busca, itens]);

  return (
    <View className="gap-1.5">
      <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
        {rotulo}
      </Texto>
      <Pressionavel
        onPress={() => setAberto(true)}
        disabled={desativado || carregando}
        escala={0.98}
        accessibilityLabel={`${rotulo}: ${escolhido?.nome ?? placeholder}`}
        style={{
          minHeight: 50,
          paddingHorizontal: 14,
          borderRadius: raios.md,
          borderWidth: 1.5,
          borderColor: erro ? '#E74C3C' : cores.borda,
          backgroundColor: cores.superficie,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
        }}>
        <Texto className="flex-1" tom={escolhido ? 'padrao' : 'fraco'} numberOfLines={1} style={{ fontFamily: fontes.media }}>
          {carregando ? 'Carregando…' : (escolhido?.nome ?? placeholder)}
        </Texto>
        <Icone nome="chevron-down" tamanho={18} cor={cores.texto3} />
      </Pressionavel>
      {erro ? (
        <Texto variante="legenda" tom="perigo">
          {erro}
        </Texto>
      ) : null}

      <Modal visible={aberto} transparent animationType="none" onRequestClose={() => setAberto(false)} statusBarTranslucent>
        {aberto ? (
          <View style={{ flex: 1, justifyContent: 'flex-end' }}>
            <Animated.View entering={FadeIn.duration(200)} exiting={FadeOut.duration(150)} style={{ position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.45)' }}>
              <Pressable style={{ flex: 1 }} onPress={() => setAberto(false)} accessibilityLabel="Fechar" />
            </Animated.View>
            <Animated.View
              entering={SlideInDown.springify().damping(22).stiffness(200)}
              exiting={SlideOutDown.duration(180)}
              style={{ maxHeight: '75%', backgroundColor: cores.fundo, borderTopLeftRadius: raios['2xl'], borderTopRightRadius: raios['2xl'], paddingBottom: insets.bottom + 8 }}>
              <View style={{ alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: cores.borda, marginTop: 10 }} />
              <View className="gap-3 px-5 pb-2 pt-4">
                <Texto variante="titulo3">{rotulo}</Texto>
                {itens.length > 8 ? <Campo icone="search" placeholder="Buscar…" value={busca} onChangeText={setBusca} autoCorrect={false} /> : null}
              </View>
              <FlatList
                data={filtrados}
                keyExtractor={(i) => String(i.id)}
                keyboardShouldPersistTaps="handled"
                contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 12 }}
                renderItem={({ item }) => {
                  const ativo = item.id === valor;
                  return (
                    <Pressionavel
                      onPress={() => {
                        aoEscolher(item.id);
                        setAberto(false);
                        setBusca('');
                      }}
                      vibrar
                      escala={0.98}
                      accessibilityState={{ selected: ativo }}
                      style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, paddingVertical: 14, borderRadius: raios.md, backgroundColor: ativo ? `${marca[500]}18` : 'transparent' }}>
                      <View className="flex-1">
                        <Texto variante={ativo ? 'corpoForte' : 'corpo'}>{item.nome}</Texto>
                        {item.detalhe ? (
                          <Texto variante="legenda" tom="fraco" numberOfLines={2}>
                            {item.detalhe}
                          </Texto>
                        ) : null}
                      </View>
                      {ativo ? <Icone nome="checkmark-circle" tamanho={22} cor={marca[500]} /> : null}
                    </Pressionavel>
                  );
                }}
                ListEmptyComponent={
                  <Texto tom="fraco" className="py-8 text-center">
                    Nada encontrado.
                  </Texto>
                }
              />
            </Animated.View>
          </View>
        ) : null}
      </Modal>
    </View>
  );
}
