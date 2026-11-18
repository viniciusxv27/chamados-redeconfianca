/**
 * Peças do Mercadinho: valor em C$, cartão do saldo, cartão de prêmio (vitrine em
 * grade), selo de status e cartão do resgate.
 */
import { Image } from 'expo-image';
import { type Href, router } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import { StyleSheet, View } from 'react-native';

import { Botao, Cartao, Icone, type NomeIcone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { PremioResumo, Resgate, StatusResgate } from '@/services/modulos/mercadinho';
import { comAlfa, tempoRelativo } from '@/utils/formatar';

export const VERDE_MERCADINHO = '#27AE60';

/** 1234 → "1.234 C$" (a web mostra sem casas decimais). */
export function cs(valor: number | null | undefined) {
  if (valor == null || Number.isNaN(valor)) return '— C$';
  return `${Math.round(valor).toLocaleString('pt-BR')} C$`;
}

/** "2026-11-30" → "30/11/2026" (data sem hora: não passa por fuso). */
export function dataCurta(iso: string | null | undefined) {
  if (!iso) return '';
  const [a, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${a}`;
}

export function irParaPremio(id: number) {
  router.push({ pathname: '/modulo/mercadinho/[id]', params: { id: String(id) } } as unknown as Href);
}

export function irParaResgates() {
  router.push('/modulo/mercadinho/resgates' as Href);
}

/** O saldo em destaque, no verde do Mercadinho. */
export function CartaoSaldo({ saldo, pendentes, aoVerResgates, aoVerExtrato }: { saldo: number | undefined; pendentes: number; aoVerResgates: () => void; aoVerExtrato: () => void }) {
  return (
    <View style={{ borderRadius: raios.lg, overflow: 'hidden', boxShadow: '0px 10px 24px rgba(39,174,96,0.28)' }}>
      <LinearGradient colors={[VERDE_MERCADINHO, '#1E8449']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={StyleSheet.absoluteFill} />
      <View style={{ padding: 20, gap: 16 }}>
        <View className="flex-row items-center gap-3">
          <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: 'rgba(255,255,255,0.18)', alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome="wallet-outline" tamanho={22} cor="#FFFFFF" />
          </View>
          <View className="flex-1">
            <Texto variante="legenda" tom="branco" style={{ opacity: 0.9 }}>
              Seu saldo
            </Texto>
            <Texto variante="titulo1" tom="branco" numberOfLines={1} adjustsFontSizeToFit>
              {cs(saldo)}
            </Texto>
          </View>
        </View>
        <View className="flex-row gap-3">
          <AcaoSaldo icone="receipt-outline" rotulo={pendentes > 0 ? `Meus resgates · ${pendentes}` : 'Meus resgates'} onPress={aoVerResgates} />
          <AcaoSaldo icone="swap-vertical-outline" rotulo="Extrato C$" onPress={aoVerExtrato} />
        </View>
      </View>
    </View>
  );
}

function AcaoSaldo({ icone, rotulo, onPress }: { icone: NomeIcone; rotulo: string; onPress: () => void }) {
  return (
    <Pressionavel
      onPress={onPress}
      vibrar
      accessibilityLabel={rotulo}
      style={{ flex: 1, height: 40, borderRadius: raios.md, backgroundColor: 'rgba(255,255,255,0.18)', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingHorizontal: 10 }}>
      <Icone nome={icone} tamanho={16} cor="#FFFFFF" />
      <Texto variante="legenda" tom="branco" numberOfLines={1} style={{ fontFamily: fontes.seminegrito, flexShrink: 1 }}>
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}

/** Aviso laranja: prêmio na SEDE esperando retirada. */
export function AvisoRetirada({ premio, retirarAte, onPress }: { premio: string; retirarAte: string; onPress: () => void }) {
  return (
    <Cartao onPress={onPress} acessibilidade={`${premio}: retire até ${dataCurta(retirarAte)}`} style={{ padding: 18, gap: 6, borderColor: comAlfa(estado.alerta, 0.5), backgroundColor: comAlfa(estado.alerta, 0.1) }}>
      <View className="flex-row items-center gap-2">
        <Icone nome="gift-outline" tamanho={18} cor={estado.alerta} />
        <Texto variante="corpoForte" className="flex-1" numberOfLines={1}>
          Pronto para retirar na SEDE
        </Texto>
        <Icone nome="chevron-forward" tamanho={18} cor={estado.alerta} />
      </View>
      <Texto variante="legenda" tom="suave">
        {`${premio} · retire até ${dataCurta(retirarAte)}`}
      </Texto>
    </Cartao>
  );
}

/** Foto do prêmio (ou o presente, sem foto). */
export function FotoPremio({ uri, altura, raio = 0 }: { uri: string | null; altura?: number; raio?: number }) {
  const cores = useCores();
  return (
    <View style={{ height: altura, aspectRatio: altura ? undefined : 1, borderRadius: raio, overflow: 'hidden', backgroundColor: cores.superficie3, alignItems: 'center', justifyContent: 'center' }}>
      {uri ? (
        <Image source={{ uri }} style={StyleSheet.absoluteFill} contentFit="cover" transition={200} accessibilityIgnoresInvertColors />
      ) : (
        <Icone nome="gift-outline" tamanho={36} cor={cores.texto3} />
      )}
    </View>
  );
}

function situacaoDoEstoque(p: PremioResumo) {
  if (!p.disponivel) return { texto: p.restantes === 0 ? 'Esgotado' : 'Indisponível', cor: estado.perigo };
  if (p.restantes == null) return { texto: 'À vontade', cor: VERDE_MERCADINHO };
  return { texto: `${p.restantes} ${p.restantes === 1 ? 'restante' : 'restantes'}`, cor: p.restantes <= 3 ? estado.alerta : VERDE_MERCADINHO };
}

/** Cartão da vitrine (meia largura, em grade). */
export function CartaoPremio({ premio }: { premio: PremioResumo }) {
  const cores = useCores();
  const estoque = situacaoDoEstoque(premio);
  return (
    <Cartao onPress={() => irParaPremio(premio.id)} acessibilidade={`${premio.nome}, ${cs(premio.valor_final)}`} style={{ overflow: 'hidden', opacity: premio.disponivel ? 1 : 0.62 }}>
      <View>
        <FotoPremio uri={premio.imagem} />
        {premio.desconto_percentual != null ? (
          <View style={{ position: 'absolute', top: 10, left: 10, backgroundColor: estado.alerta, borderRadius: raios.pilula, paddingHorizontal: 8, paddingVertical: 3 }}>
            <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.negrito, fontSize: 11 }}>
              {`-${Math.round(premio.desconto_percentual)}%`}
            </Texto>
          </View>
        ) : premio.destaque ? (
          <View style={{ position: 'absolute', top: 10, left: 10, backgroundColor: VERDE_MERCADINHO, borderRadius: raios.pilula, paddingHorizontal: 8, paddingVertical: 3 }}>
            <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.negrito, fontSize: 11 }}>
              Destaque
            </Texto>
          </View>
        ) : null}
      </View>
      <View style={{ padding: 14, gap: 6 }}>
        {premio.categoria ? (
          <Texto variante="rotulo" tom="fraco" numberOfLines={1}>
            {premio.categoria.nome}
          </Texto>
        ) : null}
        <Texto variante="corpoForte" numberOfLines={2} style={{ minHeight: 44 }}>
          {premio.nome}
        </Texto>
        <View className="flex-row flex-wrap items-baseline gap-x-2">
          <Texto variante="titulo3" style={{ color: VERDE_MERCADINHO, fontFamily: fontes.negrito }} numberOfLines={1}>
            {cs(premio.valor_final)}
          </Texto>
          {premio.desconto_percentual != null ? (
            <Texto variante="legenda" tom="fraco" style={{ textDecorationLine: 'line-through' }} numberOfLines={1}>
              {cs(premio.valor)}
            </Texto>
          ) : null}
        </View>
        <View className="flex-row items-center gap-1.5">
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: estoque.cor }} />
          <Texto variante="legenda" style={{ color: cores.texto2, fontSize: 12 }} numberOfLines={1}>
            {estoque.texto}
          </Texto>
        </View>
      </View>
    </Cartao>
  );
}

export const COR_STATUS_RESGATE: Record<StatusResgate, string> = {
  PENDENTE: estado.alerta,
  APROVADO: estado.info,
  ENTREGUE: VERDE_MERCADINHO,
  CANCELADO: estado.perigo,
};

/** Um resgate meu: foto, prêmio, valor, status e o que fazer (retirar / cancelar). */
export function CartaoResgate({ resgate, aoCancelar, cancelando }: { resgate: Resgate; aoCancelar: () => void; cancelando: boolean }) {
  const cores = useCores();
  return (
    <Cartao style={{ padding: 18, gap: 14 }}>
      <View className="flex-row items-center gap-3">
        <View style={{ width: 56 }}>
          <FotoPremio uri={resgate.premio.imagem} altura={56} raio={12} />
        </View>
        <View className="flex-1 gap-1">
          <Texto variante="corpoForte" numberOfLines={2}>
            {resgate.premio.nome}
          </Texto>
          <Texto variante="legenda" tom="fraco" numberOfLines={1}>
            {`${cs(resgate.valor)} · ${tempoRelativo(resgate.resgatado_em)}`}
          </Texto>
        </View>
        <Selo texto={resgate.status_nome} cor={COR_STATUS_RESGATE[resgate.status] ?? cores.texto3} />
      </View>
      {resgate.aguardando_retirada && resgate.retirar_ate ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderRadius: raios.md, backgroundColor: comAlfa(estado.alerta, 0.12) }}>
          <Icone nome="location-outline" tamanho={18} cor={estado.alerta} />
          <Texto variante="legenda" className="flex-1" style={{ color: cores.texto }}>
            {`Está na SEDE. Retire até ${dataCurta(resgate.retirar_ate)}.`}
          </Texto>
        </View>
      ) : resgate.status === 'PENDENTE' ? (
        <Texto variante="legenda" tom="suave">
          Aguardando aprovação. Quando chegar na SEDE, você recebe o aviso com o prazo de retirada.
        </Texto>
      ) : null}
      {resgate.observacao ? (
        <Texto variante="legenda" tom="suave">
          {resgate.observacao}
        </Texto>
      ) : null}
      {resgate.pode_cancelar ? <Botao titulo="Cancelar resgate" icone="close-circle-outline" variante="secundario" tamanho="sm" carregando={cancelando} onPress={aoCancelar} /> : null}
    </Cartao>
  );
}
