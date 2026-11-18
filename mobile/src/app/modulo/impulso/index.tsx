import { type Href, router } from 'expo-router';
import { View } from 'react-native';

import { EsqueletoBloco, Indicador, TelaModulo } from '@/components/modulos/comum';
import { BarraProgresso } from '@/components/modulos/impulso/barra';
import { pontos } from '@/components/modulos/impulso/comum';
import { BotaoIcone, Cartao, Entrada, Icone, type NomeIcone, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useRanking, useResumoImpulso } from '@/hooks/modulos/impulso';
import { useCores } from '@/providers/tema';
import type { Pontuacao, ResumoImpulso } from '@/services/modulos/impulso';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const ICONE_BLOCO: Record<string, NomeIcone> = { CONFIAR: 'shield-checkmark-outline', CONECTAR: 'school-outline', INOVAR: 'bulb-outline' };

/** Impulso: minha pontuação do mês, atalhos para as metas e o ranking. */
export default function Impulso() {
  const { data, isPending, error, refetch, isRefetching } = useResumoImpulso();

  return (
    <TelaModulo
      esqueleto="painel"
      titulo="Impulso"
      subtitulo={data ? `Mês de ${data.periodo.rotulo}` : 'Confiar · Conectar · Inovar'}
      carregando={isPending}
      erro={data ? undefined : error}
      aoAtualizar={() => void refetch()}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir o Impulso no portal" onPress={() => abrirNoPortal('/impulso/')} />}>
      {data ? <Conteudo resumo={data} /> : null}
    </TelaModulo>
  );
}

function Conteudo({ resumo }: { resumo: ResumoImpulso }) {
  const ir = (caminho: string) => router.push(caminho as Href);
  return (
    <>
      {resumo.periodo.em_conferencia ? (
        <Entrada indice={0}>
          <Cartao style={{ padding: 14, flexDirection: 'row', gap: 10, borderColor: comAlfa(estado.alerta, 0.45) }}>
            <Icone nome="hourglass-outline" tamanho={18} cor={estado.alerta} />
            <Texto variante="legenda" tom="suave" className="flex-1">
              {`O mês de ${resumo.periodo.rotulo} está em conferência. O próximo aparece depois que ele for finalizado.`}
            </Texto>
          </Cartao>
        </Entrada>
      ) : null}

      <Entrada indice={1}>
        <MinhaPontuacao minha={resumo.minha} />
      </Entrada>

      {resumo.convites > 0 ? (
        <Entrada indice={2}>
          <Atalho
            icone="people"
            cor={estado.alerta}
            titulo={resumo.convites === 1 ? '1 convite de parceria' : `${resumo.convites} convites de parceria`}
            texto="Colegas chamaram você para tocar uma meta junto."
            onPress={() => ir('/modulo/impulso/metas')}
          />
        </Entrada>
      ) : null}

      <Entrada indice={3}>
        <View className="flex-row flex-wrap gap-3">
          <Indicador rotulo="Metas em aberto" valor={String(resumo.metas.abertas)} icone="list-outline" />
          <Indicador
            rotulo="Atrasadas"
            valor={String(resumo.metas.atrasadas)}
            icone="alarm-outline"
            cor={resumo.metas.atrasadas ? estado.perigo : estado.sucesso}
            detalhe={resumo.metas.vencem_hoje ? `${resumo.metas.vencem_hoje} vence(m) hoje` : undefined}
          />
          {resumo.is_gestor ? (
            <Indicador rotulo="Entregues para você avaliar" valor={String(resumo.para_avaliar)} icone="clipboard-outline" cor={estado.alerta} />
          ) : (
            <Indicador rotulo="Entregues, aguardando avaliação" valor={String(resumo.metas.entregues)} icone="paper-plane-outline" cor={estado.alerta} />
          )}
          <Indicador
            rotulo={resumo.is_gestor ? 'Solicitações para decidir' : 'Solicitações aguardando'}
            valor={String(resumo.solicitacoes_pendentes)}
            icone="hand-left-outline"
            cor={estado.info}
          />
        </View>
      </Entrada>

      <Entrada indice={4}>
        <Atalho
          icone="albums-outline"
          cor={marca[500]}
          titulo={resumo.is_gestor ? 'Metas (minhas e da equipe)' : 'Minhas metas'}
          texto="O Kanban do Confiar: mover, marcar o to-do e comentar."
          onPress={() => ir('/modulo/impulso/metas')}
        />
      </Entrada>

      {resumo.no_ranking ? (
        <Entrada indice={5}>
          <AtalhoRanking />
        </Entrada>
      ) : (
        <Entrada indice={5}>
          <Atalho icone="podium-outline" cor="#8B5CF6" titulo="Ranking do mês" texto="Como está o Escritório neste mês." onPress={() => ir('/modulo/impulso/ranking')} />
        </Entrada>
      )}

      <Entrada indice={6}>
        <Detalhamento minha={resumo.minha} />
      </Entrada>

      <Entrada indice={7}>
        <Cartao style={{ padding: 16, gap: 10 }}>
          <Texto variante="rotulo" tom="suave">
            Faixas
          </Texto>
          {resumo.faixas.map((f) => (
            <View key={f.nome} className="flex-row items-center gap-3" style={{ opacity: f.nome === resumo.minha.faixa.nome ? 1 : 0.7 }}>
              <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: f.cor }} />
              <Texto variante={f.nome === resumo.minha.faixa.nome ? 'corpoForte' : 'legenda'} style={{ width: 70 }}>
                {f.rotulo}
              </Texto>
              <Texto variante="legenda" tom="fraco" className="flex-1">
                {f.regra}
              </Texto>
            </View>
          ))}
        </Cartao>
      </Entrada>
    </>
  );
}

function MinhaPontuacao({ minha }: { minha: Pontuacao }) {
  const cores = useCores();
  return (
    <Cartao style={{ padding: 18, gap: 16 }}>
      <View className="flex-row items-center gap-4">
        <View style={{ width: 84, height: 84, borderRadius: 42, borderWidth: 6, borderColor: minha.faixa.cor, alignItems: 'center', justifyContent: 'center', backgroundColor: comAlfa(minha.faixa.cor, 0.1) }}>
          <Texto variante="titulo2" style={{ fontSize: 22 }} adjustsFontSizeToFit numberOfLines={1}>
            {`${Math.round(minha.percentual)}%`}
          </Texto>
        </View>
        <View className="flex-1 gap-1">
          <Texto variante="rotulo" tom="fraco">
            Minha pontuação
          </Texto>
          <Texto variante="titulo3" style={{ color: minha.faixa.cor, fontFamily: fontes.negrito }}>
            {`Faixa ${minha.faixa.rotulo}`}
          </Texto>
          <Texto variante="legenda" tom="suave">
            {`${pontos(minha.total)} de ${pontos(minha.maximo)} pontos`}
          </Texto>
        </View>
      </View>
      <View className="gap-3">
        {minha.blocos.map((b) => (
          <View key={b.nome} className="gap-1.5">
            <View className="flex-row items-center gap-2">
              <Icone nome={ICONE_BLOCO[b.nome] ?? 'ellipse-outline'} tamanho={15} cor={b.cor} />
              <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito, color: cores.texto }} className="flex-1">
                {b.nome}
              </Texto>
              <Texto variante="legenda" tom="suave">
                {`${pontos(b.valor)}/${pontos(b.maximo)}`}
              </Texto>
            </View>
            <BarraProgresso pct={b.pct} cor={b.cor} />
          </View>
        ))}
      </View>
      {minha.pontos_sem_oportunidade > 0 ? (
        <Texto variante="legenda" tom="fraco">
          {`${pontos(minha.pontos_sem_oportunidade)} ponto(s) sem oportunidade neste mês (nada para fazer ainda).`}
        </Texto>
      ) : null}
    </Cartao>
  );
}

function Detalhamento({ minha }: { minha: Pontuacao }) {
  const cores = useCores();
  return (
    <Cartao style={{ padding: 16, gap: 4 }}>
      <Texto variante="rotulo" tom="suave" className="mb-1">
        De onde vêm os pontos
      </Texto>
      {minha.linhas.map((l, i) => (
        <View key={`${l.bloco}-${l.item}`} className="gap-0.5 py-2" style={{ borderTopWidth: i ? 1 : 0, borderTopColor: cores.bordaSuave }}>
          <View className="flex-row items-start gap-3">
            <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito, color: cores.texto }} className="flex-1">
              {l.item}
            </Texto>
            <Texto variante="legenda" style={{ fontFamily: fontes.negrito, color: l.pontos >= l.maximo && l.maximo > 0 ? estado.sucesso : cores.texto }}>
              {`${pontos(l.pontos)}/${pontos(l.maximo)}`}
            </Texto>
          </View>
          <Texto variante="legenda" tom="fraco">
            {`${l.bloco} · ${l.info}`}
          </Texto>
        </View>
      ))}
    </Cartao>
  );
}

function AtalhoRanking() {
  const { data, isPending, isError } = useRanking();
  if (isPending) return <EsqueletoBloco linhas={2} />;
  const texto = isError || !data ? 'Toque para ver o ranking ao vivo.' : data.minha_posicao ? `Você está em ${data.minha_posicao}º de ${data.total}.` : `${data.total} pessoas no ranking.`;
  return <Atalho icone="podium-outline" cor="#8B5CF6" titulo="Ranking do mês" texto={texto} onPress={() => router.push('/modulo/impulso/ranking' as Href)} />;
}

function Atalho({ icone, cor, titulo, texto, onPress }: { icone: NomeIcone; cor: string; titulo: string; texto: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Cartao onPress={onPress} acessibilidade={titulo} style={{ padding: 16, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
      <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: comAlfa(cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={icone} tamanho={22} cor={cor} />
      </View>
      <View className="flex-1 gap-0.5">
        <Texto variante="corpoForte">{titulo}</Texto>
        <Texto variante="legenda" tom="fraco" numberOfLines={2}>
          {texto}
        </Texto>
      </View>
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Cartao>
  );
}
