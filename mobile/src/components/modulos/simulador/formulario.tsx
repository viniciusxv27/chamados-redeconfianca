/**
 * Visão "Simulador": a tabela de valores do /simulator/ (Meta opcional, Real Individual e Real PDV /
 * Real Coordenação), pilar a pilar. Vem preenchida com o realizado do mês; a pessoa ajusta e calcula.
 * "Copiar Projeção / Copiar Realizado" troca a coluna inteira pelos valores daquela visão, e embaixo
 * do real aparece quanto falta por dia útil para bater a meta — como na web.
 */
import { useState } from 'react';
import { Alert, View } from 'react-native';

import { Botao, Campo, Cartao, Chip, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import { simulador, type FormularioSimulador } from '@/services/modulos/simulador';
import { numero, reais } from '@/utils/dinheiro';

/** Texto digitado ("1.234,56", "1234,5", "17.000") → número; vazio → null. */
export function paraNumero(texto: string | undefined) {
  const t = (texto ?? '').replace(/R\$|\s/g, '');
  if (!t) return null;
  let limpo = t;
  if (t.includes(',')) limpo = t.replace(/\./g, '').replace(',', '.');
  else if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) limpo = t.replace(/\./g, '');
  const n = Number(limpo);
  return Number.isFinite(n) ? n : null;
}

/** Valor do portal ("1234.56") → texto do campo ("1234,56"). */
export function paraCampo(valor: string | undefined) {
  return (valor ?? '').replace('.', ',');
}

export function FormularioDoSimulador({
  form,
  valores,
  aoMudar,
  aoCalcular,
  calculando,
  usuario,
  diasRestantes,
}: {
  form: FormularioSimulador;
  valores: Record<string, string>;
  aoMudar: (v: Record<string, string>) => void;
  aoCalcular: () => void;
  calculando: boolean;
  usuario?: number;
  diasRestantes: number;
}) {
  const cores = useCores();
  const [copiando, setCopiando] = useState<string | null>(null);

  async function copiar(coluna: 'meta' | 'real' | 'realpdv', visao: 'projecao' | 'realizado') {
    setCopiando(`${coluna}-${visao}`);
    try {
      const r = await simulador.valores(visao, usuario);
      const daColuna = r.valores[coluna] ?? {};
      const novo = { ...valores };
      for (const [k, v] of Object.entries(daColuna)) novo[k] = paraCampo(v);
      aoMudar(novo);
    } catch (e) {
      Alert.alert('Não deu certo', mensagemDoErro(e));
    } finally {
      setCopiando(null);
    }
  }

  return (
    <Cartao style={{ padding: 16, gap: 16 }}>
      <View className="gap-1">
        <Texto variante="corpoForte">Informe os valores para projetar</Texto>
        <Texto variante="legenda" tom="suave">
          {form.primeira ? 'Os campos vieram com o realizado do mês — ajuste o que quiser e toque em Calcular.' : 'Ajuste os valores e toque em Calcular.'} Meta vazia vale a meta oficial.
        </Texto>
      </View>

      {form.pode_copiar ? (
        <View className="gap-2">
          {form.colunas.map((c) => (
            <View key={c.chave} className="flex-row flex-wrap items-center gap-2">
              <Texto variante="legenda" tom="fraco" style={{ minWidth: 110 }}>
                {c.rotulo}
              </Texto>
              <Chip rotulo={copiando === `${c.chave}-projecao` ? 'Copiando…' : 'Copiar Projeção'} ativo={false} onPress={() => void copiar(c.chave, 'projecao')} />
              <Chip rotulo={copiando === `${c.chave}-realizado` ? 'Copiando…' : 'Copiar Realizado'} ativo={false} onPress={() => void copiar(c.chave, 'realizado')} />
            </View>
          ))}
        </View>
      ) : null}

      {form.pilares.map((p) => {
        const meta = paraNumero(valores[`${p.chave}__meta`]) ?? form.metas[p.chave] ?? null;
        const real = paraNumero(valores[p.campo_diario]) ?? 0;
        const falta = meta != null ? meta - real : 0;
        const porDia = diasRestantes > 0 && falta > 0 ? falta / diasRestantes : 0;
        return (
          <View key={p.chave} className="gap-2" style={{ paddingTop: 12, borderTopWidth: 1, borderTopColor: cores.bordaSuave }}>
            <Texto variante="corpoForte">
              {p.rotulo}
              {p.quantidade ? <Texto variante="legenda" tom="fraco"> (quantidade)</Texto> : null}
            </Texto>
            {form.colunas.map((c) =>
              (p.campos[c.chave] ?? []).map((campo) => (
                <Campo
                  key={campo.chave}
                  rotulo={`${c.rotulo}${(p.campos[c.chave] ?? []).length > 1 ? ` · ${campo.rotulo}` : ''}`}
                  placeholder={campo.dinheiro ? 'R$ 0,00' : '0'}
                  keyboardType="decimal-pad"
                  value={valores[campo.chave] ?? ''}
                  onChangeText={(v) => aoMudar({ ...valores, [campo.chave]: v })}
                />
              )),
            )}
            {meta != null ? (
              <Texto variante="legenda" style={{ color: porDia ? estado.info : estado.sucesso, fontFamily: fontes.media }}>
                {porDia
                  ? `Faltam ${p.quantidade ? numero(porDia, 1) : reais(porDia)} por dia útil para a meta (${p.quantidade ? numero(meta) : reais(meta)})`
                  : `Meta (${p.quantidade ? numero(meta) : reais(meta)}) batida com esse valor`}
              </Texto>
            ) : null}
          </View>
        );
      })}

      <Botao titulo="Calcular" icone="play" onPress={aoCalcular} carregando={calculando} />
    </Cartao>
  );
}

/** Os campos que vão ao portal: números no formato simples (1234.56), vazio fica de fora. */
export function entradasParaEnviar(valores: Record<string, string>) {
  const saida: Record<string, string> = {};
  for (const [k, v] of Object.entries(valores)) {
    const n = paraNumero(v);
    if (n != null) saida[k] = String(n);
  }
  return saida;
}
