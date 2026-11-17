/**
 * Ajuste manual de uma pessoa no Quadro de HC (o mesmo da gaveta da web): função
 * no quadro, loja onde conta, se entra na conta e o motivo. Muda só a conta do
 * quadro — o cadastro fica como está. A regra é a do portal (servicos.salvar_ajuste).
 */
import { router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';

import { Avatar, BarraDetalhe, Botao, Campo, Cartao, EsqueletoLista, EstadoErro, Segmentado, Tela, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado } from '@/constants/theme';
import { useOpcoesQuadroHC, usePessoaHC, useSalvarAjuste } from '@/hooks/modulos/quadro_hc';
import { mensagemDoErro } from '@/services/api';
import type { OpcoesQuadro, PessoaDetalhe, SituacaoAjuste } from '@/services/modulos/quadro_hc';

const SITUACOES: { valor: SituacaoAjuste; rotulo: string }[] = [
  { valor: 'AUTO', rotulo: 'Pelo cadastro' },
  { valor: 'INCLUIR', rotulo: 'Contar sempre' },
  { valor: 'EXCLUIR', rotulo: 'Não contar' },
];

export default function AjusteHC() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const pessoa = usePessoaHC(Number(id));
  const opcoes = useOpcoesQuadroHC();
  const carregando = pessoa.isPending || opcoes.isPending;
  const erro = pessoa.error ?? opcoes.error;

  return (
    <Tela>
      <BarraDetalhe titulo="Ajuste manual" />
      {carregando ? (
        <View className="px-5 pt-2">
          <EsqueletoLista quantos={4} />
        </View>
      ) : erro || !pessoa.data || !opcoes.data ? (
        <EstadoErro
          mensagem={mensagemDoErro(erro)}
          aoTentar={() => {
            void pessoa.refetch();
            void opcoes.refetch();
          }}
        />
      ) : !pessoa.data.pode_gerenciar ? (
        <EstadoErro mensagem="Seu acesso ao Quadro de HC é só para ver." />
      ) : (
        <Formulario pessoa={pessoa.data} opcoes={opcoes.data} />
      )}
    </Tela>
  );
}

function Formulario({ pessoa, opcoes }: { pessoa: PessoaDetalhe; opcoes: OpcoesQuadro }) {
  const atual = pessoa.ajuste;
  const [funcao, setFuncao] = useState(atual?.funcao ?? '');
  const [setor, setSetor] = useState<number>(atual?.setor ?? 0);
  const [situacao, setSituacao] = useState<SituacaoAjuste>(atual?.situacao ?? (pessoa.fora ? 'EXCLUIR' : 'AUTO'));
  const [motivo, setMotivo] = useState(atual?.motivo ?? '');
  const salvar = useSalvarAjuste();

  const itensFuncao = useMemo(() => [{ id: 0, nome: 'Pelo cargo do cadastro' }, ...opcoes.funcoes.map((f, i) => ({ id: i + 1, nome: f }))], [opcoes.funcoes]);
  const itensSetor = useMemo(() => [{ id: 0, nome: 'A loja do cadastro' }, ...opcoes.setores], [opcoes.setores]);
  const idFuncao = funcao ? opcoes.funcoes.indexOf(funcao) + 1 : 0;
  const linha = pessoa.fora ? `${pessoa.cargo || 'Sem cargo'} · ${pessoa.loja_cadastro}` : `${pessoa.cargo || 'Sem cargo'} · ${pessoa.loja?.nome ?? 'sem loja'}`;
  const demissao = !pessoa.fora ? pessoa.demissao_antiga : null;

  function enviar(tirar = false) {
    salvar.mutate(
      tirar
        ? { colaborador: pessoa.id, funcao: '', setor: null, situacao: 'AUTO', motivo: '' }
        : { colaborador: pessoa.id, funcao, setor: setor || null, situacao, motivo: motivo.trim() },
      { onSuccess: () => router.back() },
    );
  }

  return (
    <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
        <View className="flex-row items-center gap-3">
          <Avatar nome={pessoa.nome} tamanho={48} />
          <View className="flex-1">
            <Texto variante="corpoForte">{pessoa.nome}</Texto>
            <Texto variante="legenda" tom="suave">
              {linha}
            </Texto>
          </View>
        </View>
        <Texto variante="legenda" tom="suave">
          Muda só a conta do quadro — o cadastro fica como está. Para mudar o cadastro de verdade, use Movimentar no portal.
        </Texto>
        {demissao ? (
          <Cartao style={{ padding: 12, borderLeftWidth: 4, borderLeftColor: estado.perigo }}>
            <Texto variante="legenda">O cadastro tem demissão em {demissao.split('-').reverse().join('/')}, mas o login e o status estão ativos.</Texto>
          </Cartao>
        ) : null}

        <View className="gap-1.5">
          <Seletor rotulo="Função no quadro" placeholder="Pelo cargo do cadastro" itens={itensFuncao} valor={idFuncao} aoEscolher={(i) => setFuncao(i ? (opcoes.funcoes[i - 1] ?? '') : '')} />
          <Texto variante="legenda" tom="fraco">
            Ex.: a gerente que está com cargo de consultora no cadastro.
          </Texto>
        </View>
        <Seletor rotulo="Loja onde conta" placeholder="A loja do cadastro" itens={itensSetor} valor={setor} aoEscolher={setSetor} />
        <View className="gap-1.5">
          <Texto variante="legenda" tom="suave">
            Entra na conta?
          </Texto>
          <Segmentado opcoes={SITUACOES} valor={situacao} aoMudar={setSituacao} />
        </View>
        <Campo rotulo="Motivo" placeholder="Ex.: é a gerente da loja; cargo errado no cadastro" value={motivo} onChangeText={setMotivo} maxLength={300} multiline />

        {salvar.isError ? (
          <Texto variante="legenda" tom="perigo">
            {mensagemDoErro(salvar.error)}
          </Texto>
        ) : null}
        <Botao titulo="Salvar ajuste" icone="checkmark" carregando={salvar.isPending} onPress={() => enviar()} />
        {atual ? <Botao titulo="Tirar ajuste (seguir o cadastro)" variante="fantasma" icone="arrow-undo-outline" desativado={salvar.isPending} onPress={() => enviar(true)} /> : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
