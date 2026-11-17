import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import Animated, { FadeIn, LinearTransition } from 'react-native-reanimated';

import { EsqueletoBloco } from '@/components/modulos/comum';
import { abrirWhatsApp, BotaoWhatsApp, VERDE_WHATSAPP } from '@/components/modulos/vendas/pecas';
import { BarraDetalhe, CabecalhoGrande, Campo, Cartao, Chip, EstadoErro, EstadoVazio, Icone, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useCriarOrcamento, useOrcamentos } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { Orcamento } from '@/services/modulos/vendas';
import { tempoRelativo } from '@/utils/formatar';

/** (27) 99876-5432 enquanto digita — a mesma máscara da tela do portal. */
function mascara(v: string) {
  let d = v.replace(/\D/g, '');
  if (d.length > 11 && d.startsWith('55')) d = d.slice(2);
  d = d.slice(0, 11);
  if (d.length <= 2) return d;
  if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
  if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
}

const COR_STATUS: Record<Orcamento['status'], string> = {
  ABERTO: estado.info,
  ENVIADO: estado.sucesso,
  FALHA: estado.perigo,
  ENVIANDO: estado.alerta,
};

/**
 * Orçamento pelo WhatsApp do vendedor: o portal grava (mesmas regras do /orcamentos/) e
 * monta a mensagem; o app abre a conversa no WhatsApp do aparelho com o texto pronto.
 */
export default function NovoOrcamento() {
  const params = useLocalSearchParams<{ pilar?: string; texto?: string }>();
  const consulta = useOrcamentos();
  const criar = useCriarOrcamento();
  const [nome, setNome] = useState('');
  const [pilar, setPilar] = useState(params.pilar ?? '');
  const [numero, setNumero] = useState('');
  const [texto, setTexto] = useState(params.texto ?? '');

  const primeira = consulta.data?.pages[0];
  const historico = useMemo(() => consulta.data?.pages.flatMap((p) => p.resultados) ?? [], [consulta.data]);
  const limite = primeira?.limite ?? 2000;
  const rotuloPilar = primeira?.pilares.find((p) => p.valor === pilar)?.rotulo;
  const previa = primeira
    ? primeira.modelo
        .replace('{nome}', () => nome.trim() || '…')
        .replace('{pilar}', () => rotuloPilar ?? '…')
        .replace('{texto}', () => texto.trim() || '…')
    : '';

  function enviar() {
    criar.mutate(
      { cliente_nome: nome, pilar, numero, texto },
      {
        onSuccess: (o) => {
          void abrirWhatsApp(o.link_whatsapp, o.mensagem);
          setNome('');
          setNumero('');
          setTexto('');
        },
      },
    );
  }

  return (
    <Tela>
      <BarraDetalhe />
      {consulta.isPending ? (
        <View className="gap-4 px-5 pt-2">
          <EsqueletoBloco linhas={5} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : consulta.isError && !primeira ? (
        <EstadoErro mensagem={mensagemDoErro(consulta.error)} aoTentar={() => void consulta.refetch()} />
      ) : primeira ? (
        <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView contentContainerStyle={{ paddingBottom: 40 }} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive">
            <CabecalhoGrande titulo="Orçamento" subtitulo={`${primeira.hoje} hoje · ${primeira.total} no total`} />
            <View className="gap-4 px-5">
              <Campo rotulo="Nome do cliente" icone="person-outline" value={nome} onChangeText={setNome} maxLength={200} autoCapitalize="words" autoComplete="off" />
              <Campo
                rotulo="WhatsApp do cliente"
                icone="call-outline"
                value={numero}
                onChangeText={(v) => setNumero(mascara(v))}
                keyboardType="phone-pad"
                placeholder="(27) 99999-0000"
                maxLength={16}
              />
              <View className="gap-1.5">
                <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                  Pilar
                </Texto>
                <View className="flex-row flex-wrap gap-2">
                  {primeira.pilares.map((p) => (
                    <Chip key={p.valor} rotulo={p.rotulo} ativo={pilar === p.valor} onPress={() => setPilar(p.valor)} />
                  ))}
                </View>
              </View>
              <Campo
                rotulo="Orçamento"
                value={texto}
                onChangeText={setTexto}
                multiline
                maxLength={limite}
                placeholder="O que foi orçado, valores e condições…"
                ajuda={`${texto.length} / ${limite}`}
              />

              <View className="gap-1.5">
                <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                  Como o cliente vai receber
                </Texto>
                <View style={{ backgroundColor: '#ECE5DD', borderRadius: 18, padding: 12 }}>
                  <View style={{ backgroundColor: '#FFFFFF', borderRadius: 14, borderTopLeftRadius: 4, padding: 12 }}>
                    <Texto variante="legenda" style={{ color: '#111B21', lineHeight: 19 }}>
                      {negrito(previa)}
                    </Texto>
                  </View>
                </View>
              </View>

              {criar.isError ? (
                <Animated.View entering={FadeIn}>
                  <Cartao style={{ padding: 14, gap: 4, borderColor: estado.perigo }}>
                    {mensagemDoErro(criar.error)
                      .split('\n')
                      .map((e) => (
                        <Texto key={e} variante="legenda" tom="perigo">
                          {e}
                        </Texto>
                      ))}
                  </Cartao>
                </Animated.View>
              ) : null}
              <BotaoWhatsApp titulo="Abrir no WhatsApp do cliente" aoTocar={enviar} carregando={criar.isPending} />
              <Texto variante="legenda" tom="fraco" className="text-center">
                Abre a conversa no seu WhatsApp com a mensagem pronta. Confira e toque em enviar.
              </Texto>

              <View className="mt-4 gap-3">
                <Texto variante="titulo3">Seus orçamentos</Texto>
                {historico.length ? (
                  historico.map((o) => <ItemOrcamento key={o.id} orcamento={o} />)
                ) : (
                  <EstadoVazio icone="document-text-outline" titulo="Nenhum orçamento ainda" texto="Os orçamentos que você fizer aparecem aqui." />
                )}
                {consulta.hasNextPage ? (
                  <Pressionavel onPress={() => void consulta.fetchNextPage()} style={{ alignItems: 'center', padding: 12 }}>
                    {consulta.isFetchingNextPage ? (
                      <ActivityIndicator color={marca[500]} />
                    ) : (
                      <Texto variante="corpoForte" tom="marca">
                        Ver mais
                      </Texto>
                    )}
                  </Pressionavel>
                ) : null}
              </View>
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      ) : null}
    </Tela>
  );
}

function ItemOrcamento({ orcamento: o }: { orcamento: Orcamento }) {
  const [aberto, setAberto] = useState(false);
  const cores = useCores();
  return (
    <Animated.View layout={LinearTransition.springify().damping(20)}>
      <Cartao onPress={() => setAberto((a) => !a)} acessibilidade={`Orçamento de ${o.cliente}`} style={{ padding: 14, gap: 10 }}>
        <View className="flex-row items-center gap-3">
          <View className="flex-1 gap-0.5">
            <Texto variante="corpoForte" numberOfLines={1}>
              {o.cliente} <Texto tom="fraco">· {o.pilar_nome}</Texto>
            </Texto>
            <Texto variante="legenda" tom="fraco" numberOfLines={1}>
              {tempoRelativo(o.criado_em)} · {o.numero}
            </Texto>
          </View>
          <Selo texto={o.status_nome} cor={COR_STATUS[o.status] ?? cores.texto3} />
          <Icone nome={aberto ? 'chevron-up' : 'chevron-down'} tamanho={18} cor={cores.texto3} />
        </View>
        {aberto ? (
          <View className="gap-3">
            <View style={{ backgroundColor: cores.superficie2, borderRadius: 12, padding: 12 }}>
              <Texto variante="legenda" tom="suave" selectable>
                {o.mensagem}
              </Texto>
            </View>
            <Pressionavel
              onPress={() => void abrirWhatsApp(o.link_whatsapp, o.mensagem)}
              vibrar
              accessibilityLabel="Abrir no WhatsApp"
              style={{ flexDirection: 'row', alignItems: 'center', gap: 8, alignSelf: 'flex-start', backgroundColor: VERDE_WHATSAPP, borderRadius: 10, paddingHorizontal: 14, height: 38 }}>
              <Icone nome="logo-whatsapp" tamanho={18} cor="#FFFFFF" />
              <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.seminegrito }}>
                Abrir no WhatsApp
              </Texto>
            </Pressionavel>
          </View>
        ) : null}
      </Cartao>
    </Animated.View>
  );
}

/** *negrito* do WhatsApp na prévia. */
function negrito(texto: string) {
  return texto.split(/(\*[^*\n]+\*)/g).map((parte, i) =>
    /^\*[^*\n]+\*$/.test(parte) ? (
      <Texto key={i} variante="legenda" style={{ color: '#111B21', fontFamily: fontes.negrito }}>
        {parte.slice(1, -1)}
      </Texto>
    ) : (
      parte
    ),
  );
}
