/** Etapa 4: tudo o que vai ser gravado, para conferir antes de confirmar. */
import { View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import type { FormularioVenda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

import { Bloco } from './pecas';
import { calcItem, mascaraTel, type Rascunho, totais, valorDoPagamento } from './rascunho';

export function EtapaRevisao({ r, f }: { r: Rascunho; f: FormularioVenda }) {
  const t = totais(r, f.vivo_mais);
  const loja = f.pode_outros ? (f.lojas.find((l) => l.id === r.loja_id)?.nome ?? f.loja?.nome) : f.loja?.nome;
  const vendedor = f.pode_outros && r.vendedor_id ? f.vendedores.find((v) => v.id === r.vendedor_id)?.nome : f.eu.nome;
  return (
    <View className="gap-4">
      <Bloco titulo="Cliente">
        <Texto variante="corpoForte">{r.cliente.nome}</Texto>
        <Texto variante="legenda" tom="suave">
          {[r.cpf, r.cliente.telefone].filter(Boolean).join(' · ')}
        </Texto>
        <LinhaValor rotulo="PDV" valor={loja ?? '—'} />
        <LinhaValor rotulo="Vendedor" valor={vendedor ?? '—'} />
        {r.linhas.length ? <LinhaValor rotulo="Linhas" valor={r.linhas.map(mascaraTel).join(', ')} /> : null}
      </Bloco>

      {r.produtos.length ? (
        <Bloco titulo={`Produtos (${r.produtos.length})`}>
          {r.produtos.map((p) => {
            const c = calcItem(p, r.vivo_mais, f.vivo_mais);
            const cor = p.skus.find((s) => s.sku === p.sku)?.cor;
            return (
              <View key={p.chave} className="gap-0.5 py-1">
                <View className="flex-row justify-between gap-3">
                  <Texto variante="corpoForte" className="flex-1">
                    {p.nome}
                  </Texto>
                  <Texto variante="corpoForte" style={c.editado ? { color: estado.alerta } : undefined}>
                    {reais(c.final)}
                  </Texto>
                </View>
                <Texto variante="legenda" tom="suave">
                  {[p.sku ? `SKU ${p.sku}${cor ? ` (${cor})` : ''}` : null, p.imei ? `${p.categoria_slv === 'aparelho' ? 'IMEI' : 'Serial'} ${p.imei}` : p.prateleira_infinita ? 'Prateleira infinita' : null, p.regra]
                    .filter(Boolean)
                    .join(' · ')}
                </Texto>
              </View>
            );
          })}
        </Bloco>
      ) : null}

      {r.servicos.length ? (
        <Bloco titulo={`Serviços (${r.servicos.length})`}>
          {r.servicos.map((s) => (
            <LinhaValor key={s.chave} rotulo={`${s.rotulo}${s.descricao ? ` · ${s.descricao}` : ''}`} valor={s.valor ? `${reais(s.valor)}/mês` : '—'} />
          ))}
        </Bloco>
      ) : null}

      {r.produtos.length ? (
        <Bloco titulo="Pagamento">
          {r.pagamentos.map((pg, i) => (
            <LinhaValor
              key={i}
              rotulo={[f.formas.find((x) => x.valor === pg.forma)?.rotulo ?? '—', pg.parcelas ? `${pg.parcelas}x` : null, pg.nsu ? `NSU ${pg.nsu}` : null].filter(Boolean).join(' · ')}
              valor={reais(valorDoPagamento(r, i, t.total))}
            />
          ))}
          {r.vivo_mais ? <LinhaValor rotulo="Vivo+" valor={`${f.vivo_mais}%`} /> : null}
          {t.descontoPix ? <LinhaValor rotulo="Desconto no Pix" valor={`− ${reais(t.descontoPix)}`} cor={estado.sucesso} /> : null}
          <LinhaValor rotulo="Total" valor={reais(t.total)} destaque />
        </Bloco>
      ) : null}

      {r.observacao ? (
        <Bloco titulo="Observação">
          <Texto>{r.observacao}</Texto>
        </Bloco>
      ) : null}
    </View>
  );
}
