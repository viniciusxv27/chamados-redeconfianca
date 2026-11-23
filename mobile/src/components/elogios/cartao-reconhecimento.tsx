/**
 * Um reconhecimento, como o cartão do portal (templates/compliments/_cartao_reconhecimento.html):
 * quem foi reconhecido (foto ou iniciais; setor = ícone de equipe), o setor, as estrelas, o texto
 * (4 linhas com "Ler tudo") e, embaixo, "por Fulano" e a data. Usado na faixa da home e no mural.
 */
import { LinearGradient } from 'expo-linear-gradient';
import { useState } from 'react';
import { View } from 'react-native';

import { Avatar, Icone, Pressionavel, Texto } from '@/components/ui';
import { fontes, raios } from '@/constants/theme';
import { useTema } from '@/providers/tema';
import type { Elogio } from '@/services/tipos';
import { dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

export const COR_RECONHECIMENTO = ['#F59E0B', '#EC4899'] as const;
const ESTRELA = '#F59E0B';

function diaMes(iso: string) {
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export function CartaoReconhecimento({ elogio, largura }: { elogio: Elogio; largura?: number }) {
  const { cores, escuro } = useTema();
  const [aberto, setAberto] = useState(false);
  const [longo, setLongo] = useState(false);
  const pessoa = elogio.para_pessoa;
  const nome = pessoa?.nome ?? (elogio.para_setor ? `Setor ${elogio.para_setor}` : '—');
  const detalhe = pessoa ? (pessoa.setor ?? 'Rede Confiança') : 'Reconhecimento para a equipe';

  return (
    <LinearGradient
      colors={[...COR_RECONHECIMENTO]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{ borderRadius: raios['2xl'], padding: 1.5, width: largura, alignSelf: largura ? undefined : 'stretch' }}>
      <View style={{ flex: 1, backgroundColor: cores.superficie, borderRadius: raios['2xl'] - 1.5, padding: 16, gap: 12 }}>
        <View className="flex-row items-start gap-3">
          {pessoa ? (
            <Avatar nome={pessoa.nome} foto={pessoa.foto} tamanho={44} />
          ) : (
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: escuro ? '#3B2A1A' : '#FEF3C7', alignItems: 'center', justifyContent: 'center' }}>
              <Icone nome="people" tamanho={20} cor={ESTRELA} />
            </View>
          )}
          <View className="flex-1">
            <Texto variante="corpoForte" numberOfLines={1}>
              {nome}
            </Texto>
            <Texto variante="legenda" tom="fraco" numberOfLines={1}>
              {detalhe}
            </Texto>
            <View className="mt-1 flex-row items-center gap-0.5" accessibilityLabel={`${elogio.estrelas} de 5 estrelas`}>
              {[1, 2, 3, 4, 5].map((i) => (
                <Icone key={i} nome="star" tamanho={12} cor={i <= elogio.estrelas ? ESTRELA : cores.borda} />
              ))}
            </View>
          </View>
        </View>

        <View style={{ flex: 1 }}>
          <Texto
            tom="suave"
            numberOfLines={aberto ? undefined : 4}
            onTextLayout={(e) => {
              if (!aberto && e.nativeEvent.lines.length >= 4) setLongo(true);
            }}
            style={{ fontSize: 14, lineHeight: 20 }}>
            {elogio.comentario}
          </Texto>
          {longo ? (
            <Pressionavel onPress={() => setAberto((a) => !a)} hitSlop={8} escala={0.96} style={{ marginTop: 4, alignSelf: 'flex-start' }}>
              <Texto variante="legenda" tom="marca" style={{ fontFamily: fontes.seminegrito }}>
                {aberto ? 'Mostrar menos' : 'Ler tudo'}
              </Texto>
            </Pressionavel>
          ) : null}
        </View>

        <View className="flex-row items-center justify-between gap-2" style={{ paddingTop: 10, borderTopWidth: 1, borderTopColor: cores.bordaSuave }}>
          <View className="flex-1 flex-row items-center gap-1.5">
            <Icone nome="heart" tamanho={12} cor="#F472B6" />
            <Texto variante="legenda" tom="fraco" numberOfLines={1} className="flex-1">
              por{' '}
              <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                {elogio.de?.nome ?? 'Alguém'}
              </Texto>
            </Texto>
          </View>
          <Texto variante="legenda" tom="fraco" accessibilityLabel={dataHora(elogio.criado_em)}>
            {diaMes(elogio.criado_em)}
          </Texto>
        </View>
        {elogio.para_mim && elogio.url_web ? (
          <Pressionavel onPress={() => abrirNoPortal(elogio.url_web!)} hitSlop={8} escala={0.96} accessibilityRole="link" style={{ marginTop: -4 }}>
            <Texto variante="legenda" tom="marca" style={{ fontFamily: fontes.seminegrito }}>
              Ver detalhes →
            </Texto>
          </Pressionavel>
        ) : null}
      </View>
    </LinearGradient>
  );
}
