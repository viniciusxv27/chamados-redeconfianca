/**
 * HC previsto da loja (o quadro ideal): quantas pessoas de cada função ela deveria
 * ter. Grava pela mesma regra do portal (servicos.salvar_previsto); zero apaga.
 */
import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, View } from 'react-native';

import { corDoGrupo } from '@/components/modulos/quadro_hc/pecas';
import { BarraDetalhe, Botao, BotaoIcone, Cartao, EsqueletoLista, EstadoErro, Tela, Texto } from '@/components/ui';
import { useLojaHC, useSalvarPrevisto } from '@/hooks/modulos/quadro_hc';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { LojaDetalhe } from '@/services/modulos/quadro_hc';

const SEM_FILTRO = {};

export default function PrevistoHC() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, isPending, isError, error, refetch } = useLojaHC(Number(id), SEM_FILTRO);
  return (
    <Tela>
      <BarraDetalhe titulo="HC previsto" />
      {isPending ? (
        <View className="px-5 pt-2">
          <EsqueletoLista quantos={5} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : !data.pode_gerenciar ? (
        <EstadoErro mensagem="Seu acesso ao Quadro de HC é só para ver." />
      ) : (
        <Formulario loja={data} />
      )}
    </Tela>
  );
}

function Formulario({ loja }: { loja: LojaDetalhe }) {
  const cores = useCores();
  const [qtd, setQtd] = useState<Record<string, number>>(() => Object.fromEntries(loja.previsto.map((p) => [p.funcao, p.quantidade])));
  const salvar = useSalvarPrevisto(loja.loja.id);
  const real = Object.fromEntries(loja.loja.resumo.map((g) => [g.grupo, g.n]));
  const total = Object.values(qtd).reduce((a, b) => a + b, 0);

  function mudar(funcao: string, delta: number) {
    setQtd((q) => ({ ...q, [funcao]: Math.max(0, Math.min(99, (q[funcao] ?? 0) + delta)) }));
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }}>
      <View className="gap-1">
        <Texto variante="titulo2">{loja.loja.nome}</Texto>
        <Texto variante="legenda" tom="suave">
          Quantas pessoas de cada função a loja deveria ter. A loja passa a mostrar real × previsto e quantas faltam.
        </Texto>
      </View>
      <Cartao style={{ paddingHorizontal: 16, paddingVertical: 4 }}>
        {loja.previsto.map((p, i) => (
          <View key={p.funcao} className="flex-row items-center gap-3 py-3" style={i ? { borderTopWidth: 1, borderTopColor: cores.bordaSuave } : undefined}>
            <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: corDoGrupo(p.funcao) }} />
            <View className="flex-1">
              <Texto variante="corpo" numberOfLines={1}>
                {p.funcao}
              </Texto>
              <Texto variante="legenda" tom="fraco">
                Hoje: {real[p.funcao] ?? 0}
              </Texto>
            </View>
            <BotaoIcone icone="remove" rotulo={`Menos ${p.funcao}`} onPress={() => mudar(p.funcao, -1)} />
            <Texto variante="titulo3" style={{ width: 32, textAlign: 'center' }} accessibilityLabel={`${p.funcao}: ${qtd[p.funcao] ?? 0}`}>
              {qtd[p.funcao] ?? 0}
            </Texto>
            <BotaoIcone icone="add" rotulo={`Mais ${p.funcao}`} onPress={() => mudar(p.funcao, 1)} />
          </View>
        ))}
      </Cartao>
      <Texto variante="legenda" tom="suave">
        Total previsto: {total} {total === 1 ? 'pessoa' : 'pessoas'} (hoje são {loja.loja.total}).
      </Texto>
      {salvar.isError ? (
        <Texto variante="legenda" tom="perigo">
          {mensagemDoErro(salvar.error)}
        </Texto>
      ) : null}
      <Botao titulo="Salvar HC previsto" icone="checkmark" carregando={salvar.isPending} onPress={() => salvar.mutate(qtd, { onSuccess: () => router.back() })} />
    </ScrollView>
  );
}
