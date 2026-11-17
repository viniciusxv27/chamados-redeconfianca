import { useState } from 'react';
import { View } from 'react-native';

import { EsqueletoBloco, TelaModulo } from '@/components/modulos/comum';
import { AvisoPapel, CartaoGanho, CartaoPilarSimulador, CartaoTotais } from '@/components/modulos/simulador/pecas';
import { BotaoIcone, Chip, Entrada, EstadoVazio, Segmentado, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { usePessoasSimulador, useSimulador } from '@/hooks/modulos/simulador';
import type { ResumoSimulador, Visao } from '@/services/modulos/simulador';
import { abrirNoPortal } from '@/utils/links';

export default function Simulador() {
  const [visao, setVisao] = useState<Visao>('projecao');
  const [usuario, setUsuario] = useState<number | undefined>(undefined);
  const [acelerador, setAcelerador] = useState(false);
  const { data, isPending, error, refetch, isRefetching, isFetching } = useSimulador({ visao, usuario, acelerador });
  const pessoas = usePessoasSimulador(!!data?.pode_ver_outros);
  const lista = pessoas.data ?? [];
  const mostrarSeletor = !!data?.pode_ver_outros && (lista.length > 1 || data.papel === 'superadmin');

  const dias = data?.dias_uteis;
  const subtitulo = dias ? `${dias.restantes} ${dias.restantes === 1 ? 'dia útil restante' : 'dias úteis restantes'}` : 'Meu simulador';

  return (
    <TelaModulo
      titulo="Simulador"
      subtitulo={subtitulo}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/simulator/')} />}>
      {data ? (
        <>
          <Segmentado opcoes={data.visoes} valor={visao} aoMudar={setVisao} />
          {mostrarSeletor ? (
            <Seletor
              rotulo="Simular para"
              placeholder="Escolha uma pessoa"
              itens={lista.map((p) => ({ id: p.id, nome: p.nome, detalhe: [p.papel, p.setor].filter(Boolean).join(' · ') }))}
              valor={data.alvo?.id ?? null}
              carregando={pessoas.isPending}
              aoEscolher={setUsuario}
            />
          ) : null}
          {data.alvo?.papel === 'Consultor' ? (
            <View className="flex-row">
              <Chip rotulo="Acelerador B2B (+10%)" ativo={acelerador} onPress={() => setAcelerador((a) => !a)} />
            </View>
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
  return (
    <>
      <Entrada>
        <CartaoGanho sim={sim} visao={visao} />
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
        Estimativa do simulador com o realizado até ontem. O valor pago é o da planilha de comissão.
      </Texto>
    </>
  );
}
