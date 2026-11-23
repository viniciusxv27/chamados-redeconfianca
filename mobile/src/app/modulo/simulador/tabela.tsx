/**
 * Tabela da rede — o /simulator/tabela/ no celular: realizado, projeção, diferença e o motivo, por
 * pessoa, no recorte de quem olha (superadmin a rede; coordenador as lojas dele; gerente a loja).
 * Toque na pessoa para ver o que explica a diferença, pilar a pilar. O dado é o do dia do portal.
 */
import { useMemo, useState } from 'react';
import { View } from 'react-native';

import { Indicador, LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { Avatar, Campo, Cartao, Chip, Entrada, EstadoVazio, Icone, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useTabelaSimulador } from '@/hooks/modulos/simulador';
import { useCores } from '@/providers/tema';
import type { LinhaTabela } from '@/services/modulos/simulador';
import { porcentagem, reais } from '@/utils/dinheiro';
import { dataHora } from '@/utils/formatar';

function semAcento(t: string) {
  return t
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

export default function TabelaDaRede() {
  const { data, isPending, error, refetch, isRefetching } = useTabelaSimulador();
  const [papel, setPapel] = useState<string | null>(null);
  const [busca, setBusca] = useState('');
  const [aberta, setAberta] = useState<number | null>(null);
  const linhas = useMemo(() => {
    const termo = semAcento(busca.trim());
    return (data?.linhas ?? []).filter(
      (l) => (!papel || l.papel === papel) && (!termo || semAcento(`${l.nome} ${l.setor ?? ''} ${l.coordenador ?? ''}`).includes(termo)),
    );
  }, [data, papel, busca]);
  const soma = (k: 'realizado' | 'projecao') => linhas.reduce((s, l) => s + (l[k] || 0), 0);

  return (
    <TelaModulo
      titulo="Tabela da rede"
      subtitulo={data?.referencia ? `Realizado × projeção · ${data.referencia}` : 'Realizado × projeção'}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}>
      {data?.status === 'calculando' ? (
        <EstadoVazio icone="hourglass-outline" titulo="Montando a tabela" texto="O portal está calculando a rede (leva alguns minutos). Esta tela atualiza sozinha." />
      ) : data ? (
        <>
          {data.gerado_em ? (
            <Texto variante="legenda" tom="fraco">
              Calculado em {dataHora(data.gerado_em)}
              {data.desatualizado ? ' · atualizando' : ''}
            </Texto>
          ) : null}
          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo="Realizado" valor={reais(soma('realizado'))} icone="checkmark-done-outline" cor={estado.info} />
            <Indicador rotulo="Projeção" valor={reais(soma('projecao'))} icone="trending-up-outline" cor={estado.sucesso} detalhe={`Diferença ${reais(soma('projecao') - soma('realizado'))}`} />
          </View>
          {data.papeis.length > 1 ? (
            <View className="flex-row flex-wrap gap-2">
              <Chip rotulo="Todos" ativo={!papel} onPress={() => setPapel(null)} />
              {data.papeis.map((p) => (
                <Chip key={p.chave} rotulo={`${p.rotulo} (${p.quantos})`} ativo={papel === p.chave} onPress={() => setPapel(p.chave)} />
              ))}
            </View>
          ) : null}
          <Campo icone="search" placeholder="Buscar pessoa, loja ou coordenador…" value={busca} onChangeText={setBusca} autoCorrect={false} />
          {linhas.length ? (
            linhas.map((l, i) => (
              <Entrada key={l.id} indice={i < 8 ? i : 0}>
                <Linha linha={l} aberta={aberta === l.id} aoTocar={() => setAberta(aberta === l.id ? null : l.id)} />
              </Entrada>
            ))
          ) : (
            <EstadoVazio icone="grid-outline" titulo="Ninguém por aqui" texto="Nada no seu recorte com esse filtro." />
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function Linha({ linha: l, aberta, aoTocar }: { linha: LinhaTabela; aberta: boolean; aoTocar: () => void }) {
  const cores = useCores();
  const corDif = l.diferenca > 0.5 ? estado.sucesso : l.diferenca < -0.5 ? estado.perigo : cores.texto3;
  return (
    <Cartao onPress={aoTocar} acessibilidade={`${l.nome}: projeção ${reais(l.projecao)}`} style={{ padding: 14, gap: 10, opacity: l.tem_dado ? 1 : 0.7 }}>
      <View className="flex-row items-center gap-3">
        <Avatar nome={l.nome} tamanho={38} />
        <View className="flex-1">
          <Texto variante="corpoForte" numberOfLines={1}>
            {l.nome}
          </Texto>
          <Texto variante="legenda" tom="fraco" numberOfLines={1}>
            {[l.papel_rotulo, l.setor, l.coordenador ? `Coord. ${l.coordenador}` : null].filter(Boolean).join(' · ')}
          </Texto>
        </View>
        <View className="items-end">
          <Texto variante="corpoForte">{reais(l.projecao)}</Texto>
          <Texto variante="legenda" style={{ color: corDif, fontFamily: fontes.seminegrito }}>
            {l.diferenca > 0 ? '+' : ''}
            {reais(l.diferenca)}
          </Texto>
        </View>
        <Icone nome={aberta ? 'chevron-up' : 'chevron-down'} tamanho={16} cor={cores.texto3} />
      </View>
      <Texto variante="legenda" tom="suave" numberOfLines={aberta ? undefined : 1}>
        {l.erro ? `Erro: ${l.erro}` : l.motivo}
      </Texto>
      {aberta ? (
        <View>
          <LinhaValor rotulo="Realizado (até ontem)" valor={reais(l.realizado)} />
          <LinhaValor rotulo="Projeção do mês" valor={reais(l.projecao)} destaque />
          {l.fatores.map((f, i) => (
            <LinhaValor
              key={`${f.rotulo}-${i}`}
              rotulo={`${f.rotulo}${f.tipo === 'pilar' && f.atingimento ? ` · ${porcentagem(f.atingimento, true, 0)}` : ''}`}
              valor={`${f.delta > 0 ? '+' : ''}${reais(f.delta)}`}
              cor={f.delta > 0 ? estado.sucesso : f.delta < 0 ? estado.perigo : undefined}
            />
          ))}
          {!l.fatores.length ? (
            <Texto variante="legenda" tom="fraco" style={{ paddingVertical: 6 }}>
              Sem diferença relevante entre realizado e projeção.
            </Texto>
          ) : null}
        </View>
      ) : null}
    </Cartao>
  );
}
