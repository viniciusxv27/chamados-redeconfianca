/** Peças da tela de Metas: pilar com barra, ranking (top consultores/lojas) e a linha de um CN do gerente. */
import { useState } from 'react';
import { View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Cartao, Icone, Texto } from '@/components/ui';
import { fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { LinhaPilar, Ranking } from '@/services/modulos/power_bi_metas';
import { numero, reais } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';

/** R$ ou quantidade (FIXA quando a carga marca "FIXA em quantidade"). */
export function valorMeta(valor: number, quantidade: boolean) {
  return quantidade ? `${numero(valor)} un.` : reais(valor);
}

/** Painel por pilar: nome, valor e barra relativa ao maior pilar (como a web). */
export function PainelPilares({ titulo, linhas, cor, vazio }: { titulo: string; linhas: LinhaPilar[]; cor: string; vazio: string }) {
  const cores = useCores();
  return (
    <Cartao style={{ padding: 18, gap: 14 }}>
      <Texto variante="corpoForte">{titulo}</Texto>
      {linhas.length === 0 ? (
        <Texto variante="legenda" tom="suave">
          {vazio}
        </Texto>
      ) : (
        linhas.map((l) => (
          <View key={l.pilar} style={{ gap: 6 }}>
            <View className="flex-row items-center justify-between" style={{ gap: 12 }}>
              <Texto variante="legenda" tom="suave" className="flex-1" numberOfLines={1}>
                {l.pilar}
              </Texto>
              <Texto variante="corpoForte" style={{ color: l.quantidade ? cores.texto2 : cor }}>
                {valorMeta(l.total, l.quantidade)}
              </Texto>
            </View>
            <View style={{ height: 8, borderRadius: 4, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
              <View style={{ width: `${Math.max(0, Math.min(100, l.barra))}%`, height: '100%', borderRadius: 4, backgroundColor: l.quantidade ? cores.texto3 : cor }} />
            </View>
          </View>
        ))
      )}
    </Cartao>
  );
}

/** Top consultores / top lojas: posição, nome e total. */
export function CartaoRanking({ ranking, cor, vazio }: { ranking: Ranking; cor: string; vazio: string }) {
  const cores = useCores();
  return (
    <Cartao style={{ padding: 18, gap: 12 }}>
      <Texto variante="corpoForte">{ranking.titulo}</Texto>
      {ranking.itens.length === 0 ? (
        <Texto variante="legenda" tom="suave">
          {vazio}
        </Texto>
      ) : (
        ranking.itens.map((item, i) => (
          <View key={`${item.nome}-${i}`} className="flex-row items-center" style={{ gap: 12 }}>
            <View
              style={{
                minWidth: 34,
                height: 26,
                paddingHorizontal: 6,
                borderRadius: raios.pilula,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: i < 3 ? comAlfa(marca[500], 0.14) : cores.superficie2,
              }}>
              <Texto variante="legenda" style={{ color: i < 3 ? marca[600] : cores.texto2, fontFamily: fontes.negrito }}>
                {`#${i + 1}`}
              </Texto>
            </View>
            <Texto variante="legenda" className="flex-1" numberOfLines={1}>
              {item.nome}
            </Texto>
            <Texto variante="corpoForte" style={{ color: cor }}>
              {reais(item.total)}
            </Texto>
          </View>
        ))
      )}
    </Cartao>
  );
}

/** Uma linha da tabela CN × pilar do gerente, como cartão que abre para os pilares. */
export function CartaoVendedor({
  vendedor,
  total,
  colunas,
  celulas,
  cor,
}: {
  vendedor: string;
  total: number;
  colunas: string[];
  celulas: { valor: number; quantidade: boolean }[];
  cor: string;
}) {
  const cores = useCores();
  const [aberto, setAberto] = useState(false);
  return (
    <Cartao onPress={() => setAberto((a) => !a)} acessibilidade={`Metas de ${vendedor}`} style={{ padding: 18, gap: 10 }}>
      <View className="flex-row items-center" style={{ gap: 12 }}>
        <View className="flex-1" style={{ gap: 2 }}>
          <Texto variante="corpoForte" numberOfLines={2}>
            {vendedor}
          </Texto>
          <Texto variante="legenda" tom="fraco">
            {aberto ? 'Toque para recolher' : 'Toque para ver os pilares'}
          </Texto>
        </View>
        <Texto variante="titulo3" style={{ color: cor }}>
          {reais(total)}
        </Texto>
        <Icone nome={aberto ? 'chevron-up' : 'chevron-down'} tamanho={18} cor={cores.texto3} />
      </View>
      {aberto ? (
        <View style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave, paddingTop: 4 }}>
          {colunas.map((c, i) => (
            <LinhaValor key={c} rotulo={c} valor={valorMeta(celulas[i]?.valor ?? 0, celulas[i]?.quantidade ?? false)} />
          ))}
        </View>
      ) : null}
    </Cartao>
  );
}
