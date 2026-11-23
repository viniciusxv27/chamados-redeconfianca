/**
 * Simulador — o /simulator/ no celular: Projeção, Realizado e a visão Simulador (valores digitados,
 * pré-preenchidos com o realizado), o seletor de pessoa pelo recorte do papel, os níveis Hunter por
 * pilar e o Acelerador B2B (consultor). Quem vê o comissionamento abre também a Tabela da rede.
 * As telas de administração (fatores, lojas, snipers) seguem no portal.
 */
import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { EsqueletoBloco, TelaModulo } from '@/components/modulos/comum';
import { FormularioDoSimulador, entradasParaEnviar, paraCampo } from '@/components/modulos/simulador/formulario';
import { AvisoPapel, CartaoGanho, CartaoPilarSimulador, CartaoTotais } from '@/components/modulos/simulador/pecas';
import { BotaoIcone, Cartao, Chip, Entrada, EstadoVazio, Icone, Segmentado, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado, marca } from '@/constants/theme';
import { usePessoasSimulador, useSimulador } from '@/hooks/modulos/simulador';
import { useCores } from '@/providers/tema';
import type { ResumoSimulador, Visao } from '@/services/modulos/simulador';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const NIVEIS = [
  { valor: 0, rotulo: 'Nenhum' },
  { valor: 2, rotulo: 'Hunter 2' },
  { valor: 3, rotulo: 'Hunter 3' },
];

export default function Simulador() {
  const cores = useCores();
  const [visao, setVisao] = useState<Visao>('projecao');
  const [usuario, setUsuario] = useState<number | undefined>(undefined);
  const [acelerador, setAcelerador] = useState(false);
  const [hunter, setHunter] = useState<Record<string, number>>({});
  const [verHunter, setVerHunter] = useState(false);
  // Visão Simulador: o que foi mandado para calcular × o que está nos campos.
  const [entradas, setEntradas] = useState<Record<string, string>>({});
  const [rascunho, setRascunho] = useState<Record<string, string> | null>(null);
  const filtro = { visao, usuario, acelerador, hunter, entradas: visao === 'simulador' ? entradas : undefined };
  const { data, isPending, error, refetch, isRefetching, isFetching } = useSimulador(filtro);
  const pessoas = usePessoasSimulador(!!data?.pode_ver_outros);
  const lista = pessoas.data ?? [];
  const mostrarSeletor = !!data?.pode_ver_outros && (lista.length > 1 || data.papel === 'superadmin');
  const hunterLigados = Object.values(hunter).filter(Boolean).length;

  const dias = data?.dias_uteis;
  const subtitulo = dias ? `${dias.restantes} ${dias.restantes === 1 ? 'dia útil restante' : 'dias úteis restantes'}` : 'Meu simulador';
  // Os campos: o que a pessoa digitou ou, sem rascunho, o que o portal devolveu (realizado na 1ª vez).
  const camposDoPortal = Object.fromEntries(Object.entries(data?.simulador?.valores ?? {}).map(([k, v]) => [k, paraCampo(v)]));
  const campos = rascunho ?? camposDoPortal;

  function trocarPessoa(id: number) {
    setUsuario(id);
    setEntradas({});
    setRascunho(null);
  }

  function trocarVisao(v: Visao) {
    setVisao(v);
    if (v !== 'simulador') {
      setEntradas({});
      setRascunho(null);
    }
  }

  return (
    <TelaModulo
      esqueleto="painel"
      titulo="Simulador"
      subtitulo={subtitulo}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/simulator/')} />}>
      {data ? (
        <>
          <Segmentado opcoes={data.visoes} valor={visao} aoMudar={trocarVisao} />
          {mostrarSeletor ? (
            <Seletor
              rotulo="Simular para"
              placeholder="Escolha uma pessoa"
              itens={lista.map((p) => ({ id: p.id, nome: p.nome, detalhe: [p.papel, p.setor].filter(Boolean).join(' · ') }))}
              valor={data.alvo?.id ?? null}
              carregando={pessoas.isPending}
              aoEscolher={trocarPessoa}
            />
          ) : null}

          {/* Níveis Hunter por pilar e o Acelerador (opções da tela, como na web) */}
          <Cartao style={{ padding: 14, gap: 12 }}>
            <View className="flex-row items-center gap-3">
              <Icone nome="locate-outline" tamanho={18} cor={marca[500]} />
              <View className="flex-1">
                <Texto variante="corpoForte">Níveis Hunter</Texto>
                <Texto variante="legenda" tom="fraco">
                  {hunterLigados ? `${hunterLigados} pilar${hunterLigados > 1 ? 'es' : ''} com Hunter` : 'Nenhum pilar com Hunter'}
                </Texto>
              </View>
              <Chip rotulo={verHunter ? 'Fechar' : 'Ajustar'} ativo={verHunter} onPress={() => setVerHunter((v) => !v)} />
            </View>
            {verHunter
              ? data.pilares_hunter.map((p) => (
                  <View key={p.chave} className="gap-1.5">
                    <Texto variante="legenda" tom="suave">
                      {p.rotulo}
                    </Texto>
                    <View className="flex-row flex-wrap gap-2">
                      {NIVEIS.map((n) => (
                        <Chip key={n.valor} rotulo={n.rotulo} ativo={(hunter[p.chave] ?? 0) === n.valor} onPress={() => setHunter({ ...hunter, [p.chave]: n.valor })} />
                      ))}
                    </View>
                  </View>
                ))
              : null}
            {data.alvo?.papel === 'Consultor' ? (
              <View className="flex-row">
                <Chip rotulo={`Acelerador de Microindicadores (+${data.simulacao?.acelerador_pct ?? 10}%)`} ativo={acelerador} onPress={() => setAcelerador((a) => !a)} />
              </View>
            ) : null}
          </Cartao>

          {visao === 'simulador' && data.simulador && !data.precisa_escolher ? (
            <FormularioDoSimulador
              key={`${data.alvo?.id ?? 0}`}
              form={data.simulador}
              valores={campos}
              aoMudar={setRascunho}
              aoCalcular={() => setEntradas(entradasParaEnviar(campos))}
              calculando={isFetching && !isRefetching}
              usuario={usuario}
              diasRestantes={dias?.restantes ?? 0}
            />
          ) : null}

          {isFetching && !isRefetching ? (
            <>
              <EsqueletoBloco linhas={3} />
              <Texto variante="legenda" tom="fraco" className="text-center">
                Calculando com o realizado do mês…
              </Texto>
            </>
          ) : (
            <Resultado resumo={data} visao={visao} />
          )}

          {data.pode_ver_tabela ? (
            <Cartao onPress={() => router.push('/modulo/simulador/tabela' as Href)} acessibilidade="Tabela da rede" style={{ padding: 16, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
              <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: comAlfa(estado.info, 0.13), alignItems: 'center', justifyContent: 'center' }}>
                <Icone nome="grid-outline" tamanho={22} cor={estado.info} />
              </View>
              <View className="flex-1 gap-0.5">
                <Texto variante="corpoForte">Tabela da rede</Texto>
                <Texto variante="legenda" tom="suave">
                  Realizado × projeção de cada pessoa e o motivo da diferença.
                </Texto>
              </View>
              <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
            </Cartao>
          ) : null}
        </>
      ) : null}
    </TelaModulo>
  );
}

function Resultado({ resumo, visao }: { resumo: ResumoSimulador; visao: Visao }) {
  if (resumo.precisa_escolher) {
    return <EstadoVazio icone="people-outline" titulo="Escolha uma pessoa" texto="Selecione acima para quem você quer ver o simulador." />;
  }
  if (resumo.erro || !resumo.simulacao) {
    return <EstadoVazio icone="calculator-outline" titulo="Sem simulação" texto={resumo.erro ?? 'Nenhuma simulação disponível para esta pessoa.'} />;
  }
  const sim = resumo.simulacao;
  const visaoDoCartao: Visao = visao === 'simulador' && resumo.simulador?.primeira ? 'realizado' : visao;
  return (
    <>
      <Entrada>
        <CartaoGanho sim={sim} visao={visaoDoCartao} />
      </Entrada>
      <AvisoPapel sim={sim} />
      <Texto variante="rotulo" tom="fraco" className="mt-2">
        Pilares · toque para ver o detalhe
      </Texto>
      {sim.pilares.map((p, i) => (
        <Entrada key={p.chave} indice={i + 1}>
          <CartaoPilarSimulador pilar={p} visao={visao} />
        </Entrada>
      ))}
      <CartaoTotais sim={sim} />
      <Texto variante="legenda" tom="fraco">
        {visao === 'simulador'
          ? 'Simulação com os valores informados. O valor pago é o da planilha de comissão.'
          : 'Estimativa do simulador com o realizado até ontem. O valor pago é o da planilha de comissão.'}
      </Texto>
    </>
  );
}
