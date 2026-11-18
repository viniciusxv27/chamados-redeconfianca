import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';

import { BarraDetalhe, Botao, CabecalhoGrande, Campo, Cartao, Icone, Tela, Texto } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useSugerirProduto } from '@/hooks/modulos/mercadinho';
import { mensagemDoErro } from '@/services/api';

/** Sugerir um produto para o Mercadinho (o formulário de prizes/_sugerir_produto.html). */
export default function SugerirProduto() {
  const [link, setLink] = useState('');
  const [nome, setNome] = useState('');
  const [observacao, setObservacao] = useState('');
  const enviar = useSugerirProduto();

  const mandar = () =>
    enviar.mutate(
      { link: link.trim(), nome: nome.trim(), observacao: observacao.trim() },
      {
        onSuccess: (r) => Alert.alert('Obrigado!', r.mensagem, [{ text: 'OK', onPress: () => (router.canGoBack() ? router.back() : undefined) }]),
        onError: (erro) => Alert.alert('Não deu para enviar', mensagemDoErro(erro)),
      },
    );

  return (
    <Tela>
      <BarraDetalhe />
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ paddingBottom: 48 }} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
          <CabecalhoGrande titulo="Sugerir produto" subtitulo="Mercadinho" />
          <View style={{ paddingHorizontal: 20, gap: 18 }}>
            <Cartao style={{ padding: 18, flexDirection: 'row', gap: 12, alignItems: 'flex-start' }}>
              <Icone nome="bulb-outline" tamanho={20} cor={marca[500]} />
              <Texto variante="legenda" tom="suave" className="flex-1">
                Achou algo legal numa loja on-line? Mande o link. A empresa avalia se dá para incluir e você recebe a resposta nas notificações.
              </Texto>
            </Cartao>
            <Campo
              rotulo="Link do produto *"
              icone="link-outline"
              placeholder="https://"
              value={link}
              onChangeText={setLink}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              maxLength={2000}
            />
            <Campo rotulo="Nome do produto" placeholder="Ex.: Garrafa térmica" value={nome} onChangeText={setNome} maxLength={150} />
            <Campo rotulo="Por que sugerir" placeholder="Opcional" value={observacao} onChangeText={setObservacao} maxLength={500} multiline />
            <Botao titulo="Enviar sugestão" icone="send-outline" tamanho="lg" desativado={!link.trim()} carregando={enviar.isPending} onPress={mandar} />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Tela>
  );
}
