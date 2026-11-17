import * as Application from 'expo-application';
import { Alert, Platform, ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { Avatar, Cartao, CabecalhoGrande, Icone, type NomeIcone, Pressionavel, Segmentado, Tela, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { URL_PORTAL } from '@/services/api';
import { useSessao } from '@/providers/sessao';
import { type PreferenciaTema, useTema } from '@/providers/tema';
import { abrirNoPortal } from '@/utils/links';

export default function Perfil() {
  const { usuario, sair } = useSessao();
  const { preferencia, escolher } = useTema();

  function confirmarSaida() {
    if (Platform.OS === 'web') {
      void sair();
      return;
    }
    Alert.alert('Sair do app?', 'Você vai precisar entrar de novo com e-mail e senha.', [
      { text: 'Cancelar', style: 'cancel' },
      { text: 'Sair', style: 'destructive', onPress: () => void sair() },
    ]);
  }

  return (
    <Tela topo>
      <CabecalhoGrande titulo="Perfil" />
      <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 4, gap: 20, paddingBottom: 40 }}>
        <Animated.View entering={FadeInDown.duration(400)}>
          <Cartao style={{ padding: 20, alignItems: 'center', gap: 10 }}>
            <Avatar nome={usuario?.nome ?? '?'} foto={usuario?.foto} tamanho={84} borda={marca[500]} />
            <View className="items-center gap-1">
              <Texto variante="titulo2" className="text-center">
                {usuario?.nome}
              </Texto>
              <Texto tom="suave" className="text-center">
                {[usuario?.cargo, usuario?.setor].filter(Boolean).join(' · ') || usuario?.email}
              </Texto>
              {usuario?.hierarquia_nome ? (
                <View className="mt-1 rounded-full px-3 py-1" style={{ backgroundColor: `${marca[500]}1F` }}>
                  <Texto variante="legenda" tom="marca">
                    {usuario.hierarquia_nome}
                  </Texto>
                </View>
              ) : null}
            </View>
          </Cartao>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(80).duration(400)} className="gap-2">
          <Texto variante="rotulo" tom="fraco" className="px-1">
            Aparência
          </Texto>
          <Segmentado<PreferenciaTema>
            opcoes={[
              { valor: 'sistema', rotulo: 'Sistema' },
              { valor: 'claro', rotulo: 'Claro' },
              { valor: 'escuro', rotulo: 'Escuro' },
            ]}
            valor={preferencia}
            aoMudar={escolher}
          />
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(140).duration(400)} className="gap-2">
          <Texto variante="rotulo" tom="fraco" className="px-1">
            Conta
          </Texto>
          <Cartao style={{ overflow: 'hidden' }}>
            <Item icone="mail-outline" rotulo="E-mail" valor={usuario?.email} />
            <Divisor />
            <Item icone="call-outline" rotulo="Telefone" valor={usuario?.telefone ?? 'Não informado'} />
            <Divisor />
            <Item icone="business-outline" rotulo="Setores" valor={usuario?.setores.join(', ') || '—'} />
          </Cartao>
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(200).duration(400)} className="gap-2">
          <Texto variante="rotulo" tom="fraco" className="px-1">
            Portal
          </Texto>
          <Cartao style={{ overflow: 'hidden' }}>
            <Item icone="globe-outline" rotulo="Abrir o portal completo" onPress={() => abrirNoPortal('/')} />
            <Divisor />
            <Item icone="key-outline" rotulo="Trocar senha" onPress={() => abrirNoPortal('/users/change-password/')} />
            <Divisor />
            <Item icone="log-out-outline" rotulo="Sair" cor={estado.perigo} onPress={confirmarSaida} />
          </Cartao>
        </Animated.View>

        <Texto variante="legenda" tom="fraco" className="text-center">
          Rede Confiança · versão {Application.nativeApplicationVersion ?? '1.0.0'}
          {'\n'}
          {URL_PORTAL.replace(/^https?:\/\//, '')}
        </Texto>
      </ScrollView>
    </Tela>
  );
}

function Divisor() {
  const { cores } = useTema();
  return <View style={{ height: 1, backgroundColor: cores.bordaSuave, marginLeft: 52 }} />;
}

function Item({ icone, rotulo, valor, onPress, cor }: { icone: NomeIcone; rotulo: string; valor?: string | null; onPress?: () => void; cor?: string }) {
  const { cores } = useTema();
  const conteudo = (
    <View className="flex-row items-center gap-3 px-4 py-3.5">
      <Icone nome={icone} tamanho={20} cor={cor ?? cores.texto2} />
      <View className="flex-1">
        <Texto variante={valor ? 'legenda' : 'corpoForte'} tom={valor ? 'fraco' : 'padrao'} style={cor ? { color: cor } : undefined}>
          {rotulo}
        </Texto>
        {valor ? <Texto numberOfLines={2}>{valor}</Texto> : null}
      </View>
      {onPress ? <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} /> : null}
    </View>
  );
  return onPress ? (
    <Pressionavel onPress={onPress} escala={0.985} accessibilityLabel={rotulo}>
      {conteudo}
    </Pressionavel>
  ) : (
    conteudo
  );
}
