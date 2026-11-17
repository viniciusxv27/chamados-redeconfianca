/** Campos de data e hora com máscara (o app não tem seletor de data nativo instalado). */
import { View } from 'react-native';

import { Campo, Chip, Texto } from '@/components/ui';
import { fontes } from '@/constants/theme';
import type { Opcao } from '@/services/modulos/ponto';

import { isoDoDia, somarDias } from './pecas';

function soDigitos(v: string, max: number) {
  return v.replace(/\D/g, '').slice(0, max);
}

/** 'DD/MM/AAAA' digitado → 'AAAA-MM-DD' (ou '' se incompleto/inválido). */
export function isoDaData(texto: string) {
  const m = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!m) return '';
  const [, d, mes, a] = m;
  const data = new Date(Number(a), Number(mes) - 1, Number(d));
  if (data.getDate() !== Number(d) || data.getMonth() !== Number(mes) - 1) return '';
  return `${a}-${mes}-${d}`;
}

export function dataDoIso(iso: string | null | undefined) {
  if (!iso) return '';
  const [a, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${a}`;
}

export function mascaraData(v: string) {
  const d = soDigitos(v, 8);
  if (d.length <= 2) return d;
  if (d.length <= 4) return `${d.slice(0, 2)}/${d.slice(2)}`;
  return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`;
}

export function mascaraHora(v: string) {
  const d = soDigitos(v, 4);
  return d.length <= 2 ? d : `${d.slice(0, 2)}:${d.slice(2)}`;
}

export function CampoData({
  rotulo,
  valor,
  aoMudar,
  erro,
  atalhos = true,
  ajuda,
}: {
  rotulo: string;
  valor: string;
  aoMudar: (texto: string) => void;
  erro?: string | null;
  atalhos?: boolean;
  ajuda?: string;
}) {
  const hoje = isoDoDia(new Date());
  const rapidos = [
    { rotulo: 'Hoje', iso: hoje },
    { rotulo: 'Ontem', iso: somarDias(hoje, -1) },
    { rotulo: 'Anteontem', iso: somarDias(hoje, -2) },
  ];
  return (
    <View className="gap-2">
      <Campo
        rotulo={rotulo}
        icone="calendar-outline"
        placeholder="DD/MM/AAAA"
        keyboardType="number-pad"
        maxLength={10}
        value={valor}
        onChangeText={(v) => aoMudar(mascaraData(v))}
        erro={erro}
        ajuda={ajuda}
      />
      {atalhos ? (
        <View className="flex-row flex-wrap gap-2">
          {rapidos.map((r) => (
            <Chip key={r.rotulo} rotulo={r.rotulo} ativo={isoDaData(valor) === r.iso} onPress={() => aoMudar(dataDoIso(r.iso))} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

export function CampoHora({ rotulo, valor, aoMudar, erro }: { rotulo?: string; valor: string; aoMudar: (v: string) => void; erro?: string | null }) {
  return (
    <Campo
      rotulo={rotulo}
      icone="time-outline"
      placeholder="00:00"
      keyboardType="number-pad"
      maxLength={5}
      value={valor}
      onChangeText={(v) => aoMudar(mascaraHora(v))}
      erro={erro}
    />
  );
}

/** Escolha única em pílulas (motivo, tipo de documento). */
export function EscolhaEmChips({ rotulo, opcoes, valor, aoEscolher, erro }: { rotulo: string; opcoes: Opcao[]; valor: string; aoEscolher: (v: string) => void; erro?: string | null }) {
  return (
    <View className="gap-2">
      <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
        {rotulo}
      </Texto>
      <View className="flex-row flex-wrap gap-2">
        {opcoes.map((o) => (
          <Chip key={o.valor} rotulo={o.rotulo} ativo={valor === o.valor} onPress={() => aoEscolher(o.valor)} />
        ))}
      </View>
      {erro ? (
        <Texto variante="legenda" tom="perigo">
          {erro}
        </Texto>
      ) : null}
    </View>
  );
}
