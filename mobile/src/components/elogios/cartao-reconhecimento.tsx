/**
 * Um reconhecimento, como o cartão do portal (templates/compliments/_cartao_reconhecimento.html),
 * na mesma ordem: quem reconheceu (foto, nome e data) → o comentário (4 linhas com "Ler tudo") e as
 * estrelas → quem foi reconhecido (foto ou ícone de equipe, nome e setor). Faixa da home e mural.
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
        {/* Quem reconheceu */}
        <View className="flex-row items-center gap-2.5">
          <Avatar nome={elogio.de?.nome ?? 'Alguém'} foto={elogio.de?.foto} tamanho={36} />
          <View className="flex-1">
            <Texto variante="corpoForte" numberOfLines={1} style={{ fontSize: 14 }}>
              {elogio.de?.nome ?? 'Alguém'}
            </Texto>
            <View className="flex-row items-center gap-1">
              <Icone nome="heart" tamanho={10} cor="#F472B6" />
              <Texto variante="legenda" tom="fraco" accessibilityLabel={dataHora(elogio.criado_em)} style={{ fontSize: 11 }}>
                reconheceu · {diaMes(elogio.criado_em)}
              </Texto>
            </View>
          </View>
        </View>

        {/* O comentário */}
        <View style={{ flex: 1, backgroundColor: escuro ? '#2A2118' : '#FFF7ED', borderRadius: raios.md, paddingHorizontal: 12, paddingVertical: 10 }}>
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
          <View className="mt-1.5 flex-row items-center gap-0.5" accessibilityLabel={`${elogio.estrelas} de 5 estrelas`}>
            {[1, 2, 3, 4, 5].map((i) => (
              <Icone key={i} nome="star" tamanho={11} cor={i <= elogio.estrelas ? ESTRELA : cores.borda} />
            ))}
          </View>
        </View>

        {/* Quem foi reconhecido */}
        <View className="flex-row items-center gap-3" style={{ paddingTop: 10, borderTopWidth: 1, borderTopColor: cores.bordaSuave }}>
          {pessoa ? (
            <Avatar nome={pessoa.nome} foto={pessoa.foto} tamanho={44} />
          ) : (
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: escuro ? '#3B2A1A' : '#FEF3C7', alignItems: 'center', justifyContent: 'center' }}>
              <Icone nome="people" tamanho={20} cor={ESTRELA} />
            </View>
          )}
          <View className="flex-1">
            <Texto style={{ fontSize: 10, letterSpacing: 1, fontFamily: fontes.negrito, color: '#F97316' }}>
              {pessoa ? 'RECONHECIDO' : 'RECONHECIDOS'}
            </Texto>
            <Texto variante="corpoForte" numberOfLines={1}>
              {nome}
            </Texto>
            <Texto variante="legenda" tom="fraco" numberOfLines={1}>
              {detalhe}
            </Texto>
          </View>
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
