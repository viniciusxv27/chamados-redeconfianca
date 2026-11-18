/** Etapa 3: Vivo+, uma ou mais formas de pagamento (NSU e parcelas no cartão), desconto no Pix e observação. */
import { View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Botao, BotaoIcone, Campo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import type { FormularioVenda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

import { Bloco, CampoDinheiro, Escolha, Interruptor } from './pecas';
import { comPagamentos, moeda, mudarRascunho, type Pagamento, type Rascunho, restante, temPix, totais, valorDoPagamento } from './rascunho';

export function EtapaPagamento({ r, f }: { r: Rascunho; f: FormularioVenda }) {
  const t = totais(r, f.vivo_mais);
  if (!r.produtos.length) {
    return (
      <Bloco titulo="Pagamento">
        <Texto tom="suave">Venda só de serviços: a mensalidade do plano não é paga no balcão, então não há pagamento aqui.</Texto>
        <Observacao r={r} />
      </Bloco>
    );
  }
  const varios = r.pagamentos.length > 1;
  const mudarPg = (i: number, parcial: Partial<Pagamento>) =>
    mudarRascunho((x) => comPagamentos(x, x.pagamentos.map((pg, j) => (j === i ? { ...pg, ...parcial } : pg))));
  const pago = r.pagamentos.reduce((s, _pg, i) => s + Math.round((valorDoPagamento(r, i, t.total) ?? 0) * 100), 0) / 100;
  const dif = Math.round((t.total - pago) * 100) / 100;

  return (
    <View className="gap-4">
      <Bloco titulo="Vivo+">
        <Interruptor
          rotulo="Pago pelo Vivo+"
          detalhe={`Desconto de ${String(f.vivo_mais).replace('.', ',')}% sobre o que sobra depois do Renova.`}
          valor={r.vivo_mais}
          aoMudar={(v) => mudarRascunho((x) => ({ ...x, vivo_mais: v, produtos: x.produtos.map((p) => ({ ...p, valor_final: '' })) }))}
        />
      </Bloco>

      <Bloco titulo="Pagamento" direita={<Texto variante="corpoForte">{reais(t.total)}</Texto>}>
        {r.pagamentos.map((pg, i) => {
          const forma = f.formas.find((x) => x.valor === pg.forma);
          const auto = pg.valor === '' && i === r.pagamentos.length - 1;
          return (
            <View key={i} className="gap-3" style={varios ? { paddingTop: i ? 12 : 0 } : undefined}>
              {varios ? (
                <View className="flex-row items-center justify-between">
                  <Texto variante="rotulo" tom="fraco">
                    Parte {i + 1}
                  </Texto>
                  {i > 0 ? (
                    <BotaoIcone icone="trash-outline" rotulo={`Tirar a parte ${i + 1}`} cor={estado.perigo} onPress={() => mudarRascunho((x) => comPagamentos(x, x.pagamentos.filter((_p, j) => j !== i)))} />
                  ) : null}
                </View>
              ) : null}
              <Escolha opcoes={f.formas} valor={pg.forma || null} aoMudar={(v) => mudarPg(i, { forma: v, nsu: '', parcelas: '' })} />
              {varios ? (
                <CampoDinheiro
                  rotulo="Valor desta parte"
                  valor={pg.valor}
                  aoMudar={(v) => mudarPg(i, { valor: v })}
                  placeholder={auto ? moeda(restante(r, t.total)) : '0,00'}
                  ajuda={auto ? 'Vazio: fica com o restante da venda.' : undefined}
                />
              ) : null}
              {forma?.nsu ? (
                <Campo rotulo="NSU do comprovante *" icone="receipt-outline" value={pg.nsu} onChangeText={(v) => mudarPg(i, { nsu: v.toUpperCase() })} autoCapitalize="characters" maxLength={30} ajuda="Está no comprovante da maquininha." />
              ) : null}
              {forma?.parcelas ? (
                <Campo
                  rotulo="Parcelas *"
                  value={pg.parcelas}
                  onChangeText={(v) => mudarPg(i, { parcelas: v.replace(/\D/g, '').slice(0, 2) })}
                  keyboardType="number-pad"
                  placeholder="1 a 24"
                  ajuda="O preço do aparelho segue a coluna da parcela, quando a tabela tem."
                />
              ) : null}
            </View>
          );
        })}
        <Botao
          titulo="Dividir o pagamento"
          icone="add"
          variante="secundario"
          tamanho="sm"
          onPress={() =>
            mudarRascunho((x) => {
              const ultimo = x.pagamentos.length - 1;
              const fixo = x.pagamentos.map((pg, j) => (j === ultimo && pg.valor === '' ? { ...pg, valor: moeda(Math.max(restante(x, t.total), 0)) } : pg));
              return comPagamentos(x, [...fixo, { forma: fixo.some((pg) => pg.forma === 'CARTAO') ? 'CARTAO' : '', valor: '', nsu: '', parcelas: '' }]);
            })
          }
        />
        {varios ? (
          <Texto variante="legenda" style={{ color: dif === 0 ? estado.sucesso : dif > 0 ? estado.alerta : estado.perigo }}>
            {dif === 0 ? `Fecha com o total (${reais(t.total)})` : dif > 0 ? `Falta ${reais(dif)}` : `Passa do total em ${reais(-dif)}`}
          </Texto>
        ) : null}
      </Bloco>

      {temPix(r) ? (
        <Bloco titulo="Desconto no Pix">
          <CampoDinheiro
            rotulo="Desconto (R$)"
            valor={r.desconto_pix}
            aoMudar={(v) => mudarRascunho((x) => ({ ...x, desconto_pix: v }))}
            ajuda="Sai do total que o cliente paga; o portal reparte entre os itens."
            erro={t.descontoPix && t.descontoPix >= t.produtos ? 'O desconto não pode chegar ao total da venda.' : null}
          />
        </Bloco>
      ) : null}

      <Bloco titulo="Resumo">
        <LinhaValor rotulo="Produtos" valor={reais(t.produtos)} />
        {t.descontos ? <LinhaValor rotulo="Descontos (Renova e Vivo+)" valor={`− ${reais(t.descontos)}`} cor={estado.sucesso} /> : null}
        {t.descontoPix ? <LinhaValor rotulo="Desconto no Pix" valor={`− ${reais(t.descontoPix)}`} cor={estado.sucesso} /> : null}
        {r.servicos.length ? <LinhaValor rotulo="Planos (mensalidade, fora do total)" valor={`${reais(t.servicos)}/mês`} /> : null}
        <LinhaValor rotulo="Total a pagar" valor={reais(t.total)} destaque />
      </Bloco>

      <Bloco titulo="Observação">
        <Observacao r={r} />
      </Bloco>
    </View>
  );
}

function Observacao({ r }: { r: Rascunho }) {
  return (
    <Campo value={r.observacao} onChangeText={(v) => mudarRascunho((x) => ({ ...x, observacao: v }))} multiline maxLength={2000} placeholder="Algo que a conferência precisa saber (opcional)" />
  );
}
