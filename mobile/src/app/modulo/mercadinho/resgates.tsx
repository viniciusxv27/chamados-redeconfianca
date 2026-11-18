import { useMemo, useState } from 'react';
import { Alert, ScrollView, View } from 'react-native';

import { CartaoResgate, cs } from '@/components/modulos/mercadinho/pecas';
import { TelaLista } from '@/components/modulos/vendas/tela-lista';
import { Chip } from '@/components/ui';
import { useCancelarResgate, useResgates } from '@/hooks/modulos/mercadinho';
import { mensagemDoErro } from '@/services/api';
import type { Resgate, StatusResgate } from '@/services/modulos/mercadinho';

const FILTROS: { valor: StatusResgate | undefined; rotulo: string }[] = [
  { valor: undefined, rotulo: 'Todos' },
  { valor: 'PENDENTE', rotulo: 'Pendentes' },
  { valor: 'APROVADO', rotulo: 'Aprovados' },
  { valor: 'ENTREGUE', rotulo: 'Entregues' },
  { valor: 'CANCELADO', rotulo: 'Cancelados' },
];

/** Meus resgates: status, prazo de retirada na SEDE e cancelar o pendente (devolve o C$). */
export default function MeusResgates() {
  const [situacao, setSituacao] = useState<StatusResgate | undefined>(undefined);
  const consulta = useResgates(situacao);
  const cancelar = useCancelarResgate();
  const itens = useMemo(() => consulta.data?.pages.flatMap((p) => p.resultados) ?? [], [consulta.data]);
  const primeira = consulta.data?.pages[0];
  const contagem = primeira?.contagem;

  const pedirCancelamento = (r: Resgate) => {
    // O mesmo aviso da web (redemption_history.html:212).
    Alert.alert('Cancelar resgate', `Tem certeza que deseja cancelar "${r.premio.nome}"? O C$ será devolvido ao seu saldo.`, [
      { text: 'Voltar', style: 'cancel' },
      {
        text: 'Cancelar resgate',
        style: 'destructive',
        onPress: () =>
          cancelar.mutate(r.id, {
            onSuccess: (res) => Alert.alert('Resgate cancelado', `${res.mensagem}\nSaldo agora: ${cs(res.saldo)}.`),
            onError: (erro) => Alert.alert('Não deu para cancelar', mensagemDoErro(erro)),
          }),
      },
    ]);
  };

  return (
    <TelaLista
      titulo="Meus resgates"
      subtitulo={primeira?.saldo != null ? `Saldo: ${cs(primeira.saldo)}` : 'Mercadinho'}
      consulta={consulta}
      itens={itens}
      chave={(r) => String(r.id)}
      item={(r) => (
        // A lista de Vendas separa os cartões por 12; o Mercadinho quer 16.
        <View style={{ marginBottom: 4 }}>
          <CartaoResgate resgate={r} cancelando={cancelar.isPending && cancelar.variables === r.id} aoCancelar={() => pedirCancelamento(r)} />
        </View>
      )}
      vazio={{
        icone: 'gift-outline',
        titulo: situacao ? 'Nada por aqui' : 'Nenhum resgate ainda',
        texto: situacao ? 'Nenhum resgate com essa situação.' : 'Escolha um prêmio na vitrine e resgate com suas C$.',
      }}
      filtros={
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 20 }} style={{ marginHorizontal: -20 }}>
          {FILTROS.map((f) => {
            const n = f.valor && contagem ? contagem[f.valor] : null;
            return <Chip key={f.rotulo} rotulo={n ? `${f.rotulo} · ${n}` : f.rotulo} ativo={situacao === f.valor} onPress={() => setSituacao(f.valor)} />;
          })}
        </ScrollView>
      }
    />
  );
}
