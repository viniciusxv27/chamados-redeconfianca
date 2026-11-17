/**
 * Pessoa no Quadro de HC: função no quadro (e de onde veio), situação, admissão,
 * experiência, férias/afastado, marcas, movimentações agendadas e histórico.
 * Quem gerencia o quadro abre o ajuste manual daqui.
 */
import { useLocalSearchParams } from 'expo-router';
import { Linking, ScrollView, View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { abrirAjuste } from '@/components/modulos/quadro_hc/navegar';
import { SelosPessoa, corDoGrupo, dataCurta } from '@/components/modulos/quadro_hc/pecas';
import { Avatar, BarraDetalhe, Botao, Cartao, EsqueletoLista, EstadoErro, Icone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { usePessoaHC } from '@/hooks/modulos/quadro_hc';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { AjustePessoa, PessoaDetalhe } from '@/services/modulos/quadro_hc';

const ORIGEM = { cargo: 'Pelo cargo do cadastro', grupo: 'Pelo grupo GERENTES (lotado na loja)', manual: 'Por ajuste manual' } as const;
const SITUACAO_AJUSTE = { AUTO: 'Pelo cadastro', INCLUIR: 'Contar no quadro', EXCLUIR: 'Não contar no quadro' } as const;

export default function PessoaHC() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, isPending, isError, error, refetch } = usePessoaHC(Number(id));

  return (
    <Tela>
      <BarraDetalhe titulo="Quadro de HC" />
      {isPending ? (
        <View className="px-5 pt-2">
          <EsqueletoLista quantos={4} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }}>
          {data.fora ? <Fora pessoa={data} /> : <Dentro pessoa={data} />}
          {data.pode_gerenciar ? <Botao titulo="Ajuste manual no quadro" variante="secundario" icone="options-outline" onPress={() => abrirAjuste(data.id)} /> : null}
        </ScrollView>
      )}
    </Tela>
  );
}

function Cabeca({ nome, linha, cor }: { nome: string; linha: string; cor?: string }) {
  return (
    <View className="flex-row items-center gap-4">
      <Avatar nome={nome} tamanho={60} borda={cor} />
      <View className="flex-1 gap-1">
        <Texto variante="titulo2">{nome}</Texto>
        <Texto variante="legenda" tom="suave">
          {linha}
        </Texto>
      </View>
    </View>
  );
}

function CartaoAjuste({ ajuste }: { ajuste: NonNullable<AjustePessoa> }) {
  return (
    <Cartao style={{ padding: 16, gap: 4, borderLeftWidth: 4, borderLeftColor: '#6B7280' }}>
      <Texto variante="corpoForte">Ajuste manual</Texto>
      {ajuste.funcao ? <LinhaValor rotulo="Função no quadro" valor={ajuste.funcao} /> : null}
      {ajuste.setor_nome ? <LinhaValor rotulo="Conta na loja" valor={ajuste.setor_nome} /> : null}
      <LinhaValor rotulo="Situação" valor={SITUACAO_AJUSTE[ajuste.situacao] ?? ajuste.situacao} />
      {ajuste.motivo ? (
        <Texto variante="legenda" tom="suave" className="pt-1">
          Motivo: {ajuste.motivo}
        </Texto>
      ) : null}
    </Cartao>
  );
}

function Fora({ pessoa }: { pessoa: Extract<PessoaDetalhe, { fora: true }> }) {
  return (
    <>
      <Cabeca nome={pessoa.nome} linha={[pessoa.cargo || 'Sem cargo', pessoa.loja_cadastro].join(' · ')} />
      <Cartao style={{ padding: 14, flexDirection: 'row', gap: 10, alignItems: 'center', borderLeftWidth: 4, borderLeftColor: estado.alerta }}>
        <Icone nome="remove-circle-outline" tamanho={20} cor={estado.alerta} />
        <Texto variante="legenda" className="flex-1">
          Fora do quadro por ajuste manual: não entra na conta da loja.
        </Texto>
      </Cartao>
      {pessoa.ajuste ? <CartaoAjuste ajuste={pessoa.ajuste} /> : null}
    </>
  );
}

function corAlerta(alerta: string) {
  return alerta === 'proxima' ? estado.alerta : undefined;
}

function Dentro({ pessoa: p }: { pessoa: Extract<PessoaDetalhe, { fora: false }> }) {
  const cores = useCores();
  const local = p.loja ? [p.loja.nome, p.carteira].filter(Boolean).join(' · ') : 'Sem loja no cadastro';
  return (
    <>
      <Cabeca nome={p.nome} linha={[p.cargo || 'Sem cargo', local].join(' · ')} cor={corDoGrupo(p.grupo)} />
      <SelosPessoa pessoa={p} />

      <Cartao style={{ padding: 16, gap: 2 }}>
        <LinhaValor rotulo="Função no quadro" valor={p.grupo} destaque cor={corDoGrupo(p.grupo)} />
        <LinhaValor rotulo="De onde veio" valor={ORIGEM[p.origem_funcao]} />
        {p.origem_funcao !== 'cargo' ? <LinhaValor rotulo="Pelo cargo seria" valor={p.cargo_grupo} /> : null}
        {p.e_consultor && p.meta_pct ? <LinhaValor rotulo="Meta do consultor" valor={`${p.meta_pct}%`} /> : null}
      </Cartao>

      {p.ajuste ? <CartaoAjuste ajuste={p.ajuste} /> : null}

      <Cartao style={{ padding: 16, gap: 2 }}>
        <LinhaValor rotulo="Situação" valor={p.status || 'Ativo'} />
        {p.ferias_inicio || p.ferias_fim ? <LinhaValor rotulo="Férias" valor={`${dataCurta(p.ferias_inicio) ?? '?'} a ${dataCurta(p.ferias_fim) ?? '?'}`} /> : null}
        {p.retorno ? <LinhaValor rotulo="Volta do afastamento" valor={dataCurta(p.retorno) ?? ''} /> : null}
        <LinhaValor rotulo="Contratação" valor={dataCurta(p.contratacao) ?? '—'} />
        <LinhaValor rotulo="Experiência 1ª (45 dias)" valor={dataCurta(p.exp1) ?? '—'} cor={corAlerta(p.exp1_alerta)} destaque={p.exp1_alerta === 'proxima'} />
        <LinhaValor rotulo="Experiência 2ª (90 dias)" valor={dataCurta(p.exp2) ?? '—'} cor={corAlerta(p.exp2_alerta)} destaque={p.exp2_alerta === 'proxima'} />
        {p.sai_em ? <LinhaValor rotulo="Saída marcada" valor={dataCurta(p.sai_em) ?? ''} cor={estado.perigo} destaque /> : null}
        {p.demissao_antiga ? <LinhaValor rotulo="Demissão no cadastro" valor={dataCurta(p.demissao_antiga) ?? ''} cor={estado.perigo} destaque /> : null}
      </Cartao>

      <Cartao style={{ padding: 16, gap: 2 }}>
        <LinhaValor rotulo="Login" valor={p.login || '—'} />
        <LinhaValor rotulo="Cidade onde reside" valor={p.cidade || '—'} />
        <LinhaValor rotulo="Nascimento" valor={dataCurta(p.nascimento) ?? '—'} />
        {p.telefone ? (
          <Pressionavel onPress={() => void Linking.openURL(`tel:${p.telefone.replace(/[^\d+]/g, '')}`)} accessibilityRole="link" accessibilityLabel={`Ligar para ${p.telefone}`}>
            <View className="flex-row items-center justify-between gap-3 py-2">
              <Texto variante="legenda" tom="suave">
                Telefone
              </Texto>
              <View className="flex-row items-center gap-1.5">
                <Icone nome="call-outline" tamanho={15} cor={cores.texto2} />
                <Texto variante="legenda" tom="marca">
                  {p.telefone}
                </Texto>
              </View>
            </View>
          </Pressionavel>
        ) : (
          <LinhaValor rotulo="Telefone" valor="—" />
        )}
      </Cartao>

      {p.observacao ? (
        <Cartao style={{ padding: 16, gap: 6 }}>
          <Texto variante="corpoForte">Observação</Texto>
          <Texto variante="legenda" tom="suave">
            {p.observacao}
          </Texto>
        </Cartao>
      ) : null}

      {p.agendadas.length ? (
        <Cartao style={{ padding: 16, gap: 8 }}>
          <Texto variante="corpoForte">Movimentações agendadas</Texto>
          {p.agendadas.map((a) => (
            <View key={a.id} className="flex-row gap-2">
              <Icone nome="calendar-outline" tamanho={16} cor={estado.alerta} />
              <Texto variante="legenda" className="flex-1">
                {dataCurta(a.data)}: {a.resumo}
              </Texto>
            </View>
          ))}
        </Cartao>
      ) : null}

      {p.historico.length ? (
        <Cartao style={{ padding: 16, gap: 10 }}>
          <Texto variante="corpoForte">Histórico</Texto>
          {p.historico.map((h, i) => (
            <View key={i} className="flex-row gap-2.5">
              <View style={{ width: 8, height: 8, borderRadius: 4, marginTop: 5, backgroundColor: h.origem === 'quadro' ? estado.sucesso : cores.texto3 }} />
              <View className="flex-1 gap-0.5">
                <Texto variante="legenda">{h.frase}</Texto>
                <Texto variante="legenda" tom="fraco">
                  {h.quando}
                </Texto>
              </View>
            </View>
          ))}
        </Cartao>
      ) : null}
    </>
  );
}
