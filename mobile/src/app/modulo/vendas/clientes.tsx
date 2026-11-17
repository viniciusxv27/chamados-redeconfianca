import { useMemo, useState } from 'react';
import { ScrollView } from 'react-native';

import { CartaoCliente } from '@/components/modulos/vendas/pecas';
import { TelaLista, useBuscaAdiada } from '@/components/modulos/vendas/tela-lista';
import { Campo, Chip } from '@/components/ui';
import { useClientes } from '@/hooks/modulos/vendas';
import type { SituacaoCliente } from '@/services/modulos/vendas';

const SITUACOES: { valor: SituacaoCliente; rotulo: string }[] = [
  { valor: '', rotulo: 'Todos' },
  { valor: 'ativos', rotulo: 'Compraram em 30 dias' },
  { valor: 'novos', rotulo: 'Novos no mês' },
  { valor: 'recorrentes', rotulo: 'Recorrentes' },
  { valor: 'sumidos', rotulo: 'Sumidos' },
  { valor: 'portal', rotulo: 'Do portal' },
];

/** Clientes da loja (ou da rede, para quem vê a rede): base do Vivo GO + cadastro do portal. */
export default function Clientes() {
  const [situacao, setSituacao] = useState<SituacaoCliente>('');
  const { texto, setTexto, busca } = useBuscaAdiada();
  const filtro = useMemo(() => ({ busca: busca || undefined, situacao: situacao || undefined }), [busca, situacao]);
  const consulta = useClientes(filtro);
  const itens = useMemo(() => consulta.data?.pages.flatMap((p) => p.resultados) ?? [], [consulta.data]);
  const total = consulta.data?.pages[0]?.total ?? 0;

  return (
    <TelaLista
      titulo="Clientes"
      subtitulo={consulta.isPending ? ' ' : `${total} ${total === 1 ? 'cliente' : 'clientes'}`}
      consulta={consulta}
      itens={itens}
      chave={(c) => c.cpf}
      item={(c) => <CartaoCliente cliente={c} />}
      vazio={{
        icone: busca ? 'search-outline' : 'people-outline',
        titulo: busca ? 'Nada encontrado' : 'Nenhum cliente',
        texto: busca ? `Nenhum cliente com "${busca}".` : 'Nenhum cliente neste filtro.',
      }}
      filtros={
        <>
          <Campo icone="search" placeholder="Nome, CPF, telefone ou linha" value={texto} onChangeText={setTexto} returnKeyType="search" autoCorrect={false} clearButtonMode="while-editing" />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
            {SITUACOES.map((s) => (
              <Chip key={s.valor || 'todos'} rotulo={s.rotulo} ativo={situacao === s.valor} onPress={() => setSituacao(s.valor)} />
            ))}
          </ScrollView>
        </>
      }
    />
  );
}
