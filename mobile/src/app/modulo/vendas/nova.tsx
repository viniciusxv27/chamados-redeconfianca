import { type Href, router } from 'expo-router';
import { type ComponentRef, useRef, useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { EtapaCliente } from '@/components/modulos/vendas/lancar/etapa-cliente';
import { EtapaItens } from '@/components/modulos/vendas/lancar/etapa-itens';
import { EtapaPagamento } from '@/components/modulos/vendas/lancar/etapa-pagamento';
import { EtapaRevisao } from '@/components/modulos/vendas/lancar/etapa-revisao';
import { CaixaErros } from '@/components/modulos/vendas/lancar/pecas';
import {
  descartarRascunho,
  errosCliente,
  errosItens,
  errosPagamento,
  ETAPAS,
  montarPayload,
  mudarRascunho,
  type Rascunho,
  temRascunho,
  totais,
  useRascunho,
} from '@/components/modulos/vendas/lancar/rascunho';
import { BarraDetalhe, Botao, BotaoGradiente, BotaoIcone, EsqueletoDetalhe, EstadoErro, EstadoVazio, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useFormularioVenda, useGravarVenda } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ErroDaVenda, FormularioVenda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

type ErrosPorEtapa = Partial<Record<number, string[]>>;

/** Do erro do servidor (por etapa do slv.registrar_venda) para a etapa da tela. */
function etapasDoServidor(e: ErroDaVenda['etapas']): ErrosPorEtapa {
  const saida: ErrosPorEtapa = {};
  const por = (i: number, l?: string[]) => {
    if (l?.length) saida[i] = [...(saida[i] ?? []), ...l];
  };
  por(0, e.cliente);
  por(1, e.produtos);
  por(1, e.servicos);
  por(2, e.pagamento);
  return saida;
}

/**
 * Lançar venda em etapas (Cliente → Itens → Pagamento → Revisão). O rascunho fica em memória
 * entre as etapas; cada etapa confere o que a web confere, e o servidor decide no fim
 * (o mesmo slv.registrar_venda da tela /vendas/nova/).
 */
export default function NovaVenda() {
  const formulario = useFormularioVenda();
  const f = formulario.data;

  return (
    <Tela>
      <BarraDetalhe titulo="Nova venda" direita={<Descartar />} />
      {formulario.isPending ? (
        <EsqueletoDetalhe />
      ) : formulario.isError || !f ? (
        <EstadoErro mensagem={mensagemDoErro(formulario.error)} aoTentar={() => void formulario.refetch()} />
      ) : f.sem_pdv ? (
        <EstadoVazio icone="storefront-outline" titulo="Seu usuário não tem PDV vinculado" texto="A venda sai sempre no PDV do seu cadastro no portal. Procure o administrador do portal para vincular a sua loja." />
      ) : (
        <Fluxo f={f} />
      )}
    </Tela>
  );
}

function Descartar() {
  const r = useRascunho();
  if (!temRascunho(r)) return <View style={{ width: 40 }} />;
  return (
    <BotaoIcone
      icone="trash-outline"
      rotulo="Descartar a venda"
      onPress={() =>
        Alert.alert('Descartar a venda?', 'Nada foi gravado. O que você preencheu será apagado.', [
          { text: 'Continuar preenchendo', style: 'cancel' },
          { text: 'Descartar', style: 'destructive', onPress: descartarRascunho },
        ])
      }
    />
  );
}

function Fluxo({ f }: { f: FormularioVenda }) {
  const r = useRascunho();
  const insets = useSafeAreaInsets();
  const cores = useCores();
  const gravar = useGravarVenda();
  const rolagem = useRef<ComponentRef<typeof ScrollView>>(null);
  const [erros, setErros] = useState<ErrosPorEtapa>({});
  const t = totais(r, f.vivo_mais);
  const ultima = ETAPAS.length - 1;

  const conferir = (x: Rascunho, i: number) =>
    i === 0 ? errosCliente(x, f.sem_pdv) : i === 1 ? errosItens(x, f.vivo_mais) : i === 2 ? errosPagamento(x, f.vivo_mais) : [];

  function irPara(i: number) {
    mudarRascunho((x) => ({ ...x, etapa: i }));
    rolagem.current?.scrollTo({ y: 0, animated: false });
  }

  function continuar() {
    if (r.etapa < ultima) {
      const e = conferir(r, r.etapa);
      setErros((x) => ({ ...x, [r.etapa]: e }));
      if (e.length) return rolagem.current?.scrollToEnd({ animated: true });
      return irPara(r.etapa + 1);
    }
    // Revisão: confere tudo de novo e manda; o servidor decide.
    const todas: ErrosPorEtapa = { 0: conferir(r, 0), 1: conferir(r, 1), 2: conferir(r, 2) };
    const primeira = [0, 1, 2].find((i) => todas[i]?.length);
    setErros(todas);
    if (primeira !== undefined) return irPara(primeira);
    gravar.mutate(montarPayload(r, f.vivo_mais, f.pode_outros), {
      onSuccess: (res) => {
        if (res.ok) {
          descartarRascunho();
          setErros({});
          router.replace({ pathname: '/modulo/vendas/[id]', params: { id: String(res.id) } } as unknown as Href);
          return;
        }
        const porEtapa = etapasDoServidor(res.etapas ?? {});
        if (!Object.keys(porEtapa).length) porEtapa[ultima] = res.erros?.length ? res.erros : [res.erro];
        setErros(porEtapa);
        const alvo = [0, 1, 2, ultima].find((i) => porEtapa[i]?.length) ?? ultima;
        irPara(alvo);
      },
      onError: (e) => setErros({ [ultima]: [mensagemDoErro(e)] }),
    });
  }

  const daEtapa = erros[r.etapa] ?? [];

  return (
    <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <Progresso etapa={r.etapa} erros={erros} aoTocar={(i) => (i < r.etapa ? irPara(i) : undefined)} />
      <ScrollView ref={rolagem} contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 24 }} keyboardShouldPersistTaps="handled" keyboardDismissMode="interactive">
        <Animated.View key={r.etapa} entering={FadeIn.duration(220)} className="gap-4">
          {r.etapa === 0 ? <EtapaCliente r={r} f={f} /> : r.etapa === 1 ? <EtapaItens r={r} f={f} /> : r.etapa === 2 ? <EtapaPagamento r={r} f={f} /> : <EtapaRevisao r={r} f={f} />}
        </Animated.View>
        <CaixaErros erros={daEtapa} titulo={r.etapa === ultima ? 'O portal não gravou a venda' : 'Corrija antes de continuar'} />
      </ScrollView>
      <View
        className="gap-3 px-5 pt-3"
        style={{ paddingBottom: Math.max(insets.bottom, 12), borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie }}>
        <View className="flex-row items-center justify-between">
          <Texto variante="legenda" tom="suave">
            {r.produtos.length} {r.produtos.length === 1 ? 'produto' : 'produtos'} · {r.servicos.length} {r.servicos.length === 1 ? 'serviço' : 'serviços'}
          </Texto>
          <Texto variante="titulo3">{reais(t.total)}</Texto>
        </View>
        <View className="flex-row gap-3">
          {r.etapa > 0 ? (
            <View style={{ width: 110 }}>
              <Botao titulo="Voltar" variante="secundario" icone="chevron-back" onPress={() => irPara(r.etapa - 1)} desativado={gravar.isPending} />
            </View>
          ) : null}
          <View style={{ flex: 1 }}>
            <BotaoGradiente
              titulo={r.etapa === ultima ? 'Confirmar venda' : `Continuar · ${ETAPAS[r.etapa + 1]}`}
              icone={r.etapa === ultima ? 'checkmark' : 'chevron-forward'}
              onPress={continuar}
              carregando={gravar.isPending}
            />
          </View>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

function Progresso({ etapa, erros, aoTocar }: { etapa: number; erros: ErrosPorEtapa; aoTocar: (i: number) => void }) {
  const cores = useCores();
  return (
    <View className="flex-row gap-2 px-5 pb-3" accessibilityRole="progressbar" accessibilityValue={{ min: 1, max: ETAPAS.length, now: etapa + 1 }}>
      {ETAPAS.map((nome, i) => {
        const feita = i < etapa;
        const atual = i === etapa;
        const comErro = !!erros[i]?.length && !atual;
        const cor = comErro ? estado.perigo : feita || atual ? marca[500] : cores.superficie3;
        return (
          <Pressionavel key={nome} onPress={() => aoTocar(i)} disabled={!feita} style={{ flex: 1, gap: 6 }} accessibilityLabel={`Etapa ${i + 1}: ${nome}`}>
            <View style={{ height: 5, borderRadius: 3, backgroundColor: cor, opacity: atual || feita || comErro ? 1 : 0.8 }} />
            <Texto variante="legenda" numberOfLines={1} style={{ color: atual ? cores.texto : comErro ? estado.perigo : cores.texto3, fontFamily: atual ? fontes.negrito : fontes.media, fontSize: 12 }}>
              {i + 1}. {nome}
            </Texto>
          </Pressionavel>
        );
      })}
    </View>
  );
}
