/**
 * Peças do módulo Vendas: cartões de venda/produto/cliente, seção com título,
 * barrinhas dos últimos dias e o "abrir no WhatsApp" do próprio aparelho.
 */
import { type Href, router } from 'expo-router';
import type { ReactNode } from 'react';
import { Linking, Share, View } from 'react-native';

import { Cartao, Icone, type NomeIcone, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { ClienteResumo, ProdutoResumo, VendaResumo } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';
import { comAlfa, tempoRelativo } from '@/utils/formatar';

export const COR_VENDAS = '#F39C12';
export const VERDE_WHATSAPP = '#25D366';

/** Título de bloco com ícone, e o conteúdo dentro de um cartão. */
export function Secao({ titulo, icone, direita, children }: { titulo: string; icone: NomeIcone; direita?: ReactNode; children: ReactNode }) {
  const cores = useCores();
  return (
    <View className="gap-2">
      <View className="flex-row items-center gap-2 px-1">
        <Icone nome={icone} tamanho={16} cor={cores.texto3} />
        <Texto variante="rotulo" tom="fraco" className="flex-1">
          {titulo}
        </Texto>
        {direita}
      </View>
      <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>{children}</Cartao>
    </View>
  );
}

/** Ícone redondo colorido (atalhos e cabeçalhos dos cartões). */
export function Bolinha({ icone, cor, tamanho = 40 }: { icone: NomeIcone; cor: string; tamanho?: number }) {
  return (
    <View style={{ width: tamanho, height: tamanho, borderRadius: tamanho / 3, backgroundColor: comAlfa(cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
      <Icone nome={icone} tamanho={tamanho * 0.5} cor={cor} />
    </View>
  );
}

export function irParaVenda(id: number) {
  router.push({ pathname: '/modulo/vendas/[id]', params: { id: String(id) } } as unknown as Href);
}

export function CartaoVenda({ venda, semToque = false }: { venda: VendaResumo; semToque?: boolean }) {
  const cores = useCores();
  const quem = [venda.loja, venda.vendedor].filter(Boolean).join(' · ');
  return (
    <Cartao onPress={semToque ? undefined : () => irParaVenda(venda.id)} acessibilidade={`Venda ${venda.id} de ${venda.cliente}`} style={{ padding: 14, gap: 8 }}>
      <View className="flex-row items-start gap-3">
        <View className="flex-1 gap-0.5">
          <Texto variante="corpoForte" numberOfLines={1}>
            {venda.cliente}
          </Texto>
          <Texto variante="legenda" tom="fraco" numberOfLines={1}>
            #{venda.id} · {tempoRelativo(venda.data)}
            {quem ? ` · ${quem}` : ''}
          </Texto>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Texto variante="corpoForte">{reais(venda.total)}</Texto>
          {venda.total_servicos != null ? (
            <Texto variante="legenda" style={{ color: '#DB2777', fontFamily: fontes.seminegrito }}>
              {reais(venda.total_servicos)}/mês
            </Texto>
          ) : null}
        </View>
      </View>
      {venda.itens.length ? (
        <Texto variante="legenda" tom="suave" numberOfLines={2}>
          {venda.itens.join(' · ')}
          {venda.mais_itens ? ` +${venda.mais_itens}` : ''}
        </Texto>
      ) : null}
      <View className="flex-row flex-wrap gap-2">
        {venda.tipo_venda ? <Selo texto={venda.tipo_venda} cor={COR_VENDAS} ponto={false} /> : null}
        {venda.forma_pagamento ? <Selo texto={venda.forma_pagamento} cor={cores.texto3} ponto={false} /> : null}
        {venda.numero_fake ? <Selo texto="Número fictício" cor={estado.perigo} /> : null}
      </View>
    </Cartao>
  );
}

export function irParaProduto(id: number) {
  router.push({ pathname: '/modulo/vendas/preco/[id]', params: { id: String(id) } } as unknown as Href);
}

export function CartaoProduto({ produto }: { produto: ProdutoResumo }) {
  const detalhe = [produto.marca, produto.tipo].filter(Boolean).join(' · ');
  return (
    <Cartao onPress={() => irParaProduto(produto.id)} acessibilidade={`Abrir ${produto.nome}`} style={{ padding: 14, gap: 8 }}>
      <View className="flex-row items-center gap-3">
        <Bolinha icone={iconeDaCategoria(produto.categoria)} cor={marca[500]} tamanho={40} />
        <View className="flex-1 gap-0.5">
          <Texto variante="corpoForte" numberOfLines={2}>
            {produto.nome}
          </Texto>
          {detalhe ? (
            <Texto variante="legenda" tom="fraco" numberOfLines={1}>
              {detalhe}
            </Texto>
          ) : null}
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Texto variante="legenda" tom="fraco">
            a partir de
          </Texto>
          <Texto variante="corpoForte">{reais(produto.a_partir)}</Texto>
          {produto.pix != null ? (
            <Texto variante="legenda" tom="sucesso" style={{ fontFamily: fontes.seminegrito }}>
              Pix {reais(produto.pix)}
            </Texto>
          ) : null}
        </View>
      </View>
      {produto.sku_digitado ? (
        <Selo texto={`SKU ${produto.sku_digitado.sku}${produto.sku_digitado.cor ? ` · ${produto.sku_digitado.cor}` : ''}`} cor={estado.info} />
      ) : produto.cores > 1 ? (
        <Selo texto={`${produto.cores} cores`} cor={estado.info} ponto={false} />
      ) : null}
    </Cartao>
  );
}

export function iconeDaCategoria(categoria: string): NomeIcone {
  if (categoria.startsWith('SMARTPHONES')) return 'phone-portrait-outline';
  if (categoria.includes('Não Conectados')) return 'headset-outline';
  if (categoria.includes('Conectados')) return 'wifi-outline';
  return 'pricetag-outline';
}

export function CartaoCliente({ cliente }: { cliente: ClienteResumo }) {
  return (
    <Cartao
      onPress={() => router.push({ pathname: '/modulo/vendas/cliente/[cpf]', params: { cpf: cliente.cpf } } as unknown as Href)}
      acessibilidade={`Abrir ${cliente.nome}`}
      style={{ padding: 14, gap: 6 }}>
      <View className="flex-row items-center gap-3">
        <Bolinha icone={cliente.cpf.length === 14 ? 'business-outline' : 'person-outline'} cor={estado.info} tamanho={38} />
        <View className="flex-1 gap-0.5">
          <Texto variante="corpoForte" numberOfLines={1}>
            {cliente.nome}
          </Texto>
          <Texto variante="legenda" tom="fraco" numberOfLines={1}>
            {cliente.cpf_formatado}
            {cliente.telefone ? ` · ${cliente.telefone}` : ''}
          </Texto>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Texto variante="corpoForte">{cliente.qtd_vendas}</Texto>
          <Texto variante="legenda" tom="fraco">
            {cliente.qtd_vendas === 1 ? 'compra' : 'compras'}
          </Texto>
        </View>
      </View>
    </Cartao>
  );
}

/** Barrinhas dos últimos dias (vendas por dia). */
export function Barras({ serie }: { serie: { dia: string; n: number }[] }) {
  const cores = useCores();
  const pico = Math.max(1, ...serie.map((d) => d.n));
  const ultimo = serie.length - 1;
  return (
    <View accessibilityLabel={`Vendas por dia nos últimos ${serie.length} dias`} className="gap-1.5 py-2">
      <View className="flex-row items-end gap-1" style={{ height: 72 }}>
        {serie.map((d, i) => (
          <View key={d.dia} style={{ flex: 1, height: '100%', justifyContent: 'flex-end' }}>
            <View
              style={{
                height: `${Math.max(d.n ? 8 : 3, (d.n / pico) * 100)}%`,
                borderRadius: 4,
                backgroundColor: i === ultimo ? marca[500] : d.n ? comAlfa(COR_VENDAS, 0.55) : cores.superficie3,
              }}
            />
          </View>
        ))}
      </View>
      <View className="flex-row justify-between">
        <Texto variante="legenda" tom="fraco">
          {rotuloDia(serie[0]?.dia)}
        </Texto>
        <Texto variante="legenda" tom="fraco">
          hoje
        </Texto>
      </View>
    </View>
  );
}

function rotuloDia(iso?: string) {
  if (!iso) return '';
  const [a, m, d] = iso.split('-').map(Number);
  return new Date(a, m - 1, d).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

/** "2026-11-17" → "17/11/2026" (data sem hora, sem fuso). */
export function dataCurta(iso: string | null | undefined) {
  if (!iso) return '—';
  const [a, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${a}`;
}

/**
 * Abre a conversa no WhatsApp do próprio aparelho com a mensagem pronta (wa.me abre o
 * aplicativo). Sem WhatsApp, cai no compartilhar do sistema. O portal não envia nada.
 */
export async function abrirWhatsApp(link: string, mensagem: string) {
  try {
    await Linking.openURL(link);
  } catch {
    await Share.share({ message: mensagem });
  }
}

export function BotaoWhatsApp({ aoTocar, titulo = 'Abrir no WhatsApp', carregando }: { aoTocar: () => void; titulo?: string; carregando?: boolean }) {
  return (
    <Cartao
      onPress={carregando ? undefined : aoTocar}
      acessibilidade={titulo}
      style={{ backgroundColor: VERDE_WHATSAPP, borderColor: VERDE_WHATSAPP, height: 50, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 8, opacity: carregando ? 0.7 : 1 }}>
      <Icone nome="logo-whatsapp" tamanho={20} cor="#FFFFFF" />
      <Texto variante="corpoForte" tom="branco">
        {carregando ? 'Salvando…' : titulo}
      </Texto>
    </Cartao>
  );
}
