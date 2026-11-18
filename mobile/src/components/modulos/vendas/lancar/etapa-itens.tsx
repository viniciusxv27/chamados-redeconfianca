/** Etapa 2: produtos (cor/SKU, grupo e condição do preço, IMEI/serial, Renova, valor final), serviços e linhas. */
import { useState } from 'react';
import { View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Botao, BotaoIcone, Campo, Chip, Icone, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useConferirRenova } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import type { FormularioVenda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

import { BuscaProduto } from './busca-produto';
import { Bloco, CampoDinheiro, Escolha, Interruptor } from './pecas';
import {
  aplicarTabela,
  calcItem,
  COND_BASE,
  fake,
  imeiValido,
  mascaraTel,
  mudarRascunho,
  novoProduto,
  type ProdutoRascunho,
  type Rascunho,
  rotuloCondicao,
  so,
  telValido,
} from './rascunho';
import { ServicoForm } from './servico-form';

export function EtapaItens({ r, f }: { r: Rascunho; f: FormularioVenda }) {
  const [buscando, setBuscando] = useState(false);
  const [servico, setServico] = useState(false);
  const seg = r.plano_anterior.segmentacao;

  return (
    <View className="gap-4">
      <Bloco titulo={`Produtos (${r.produtos.length})`} direita={<Botao titulo="Adicionar" icone="add" tamanho="sm" larguraTotal={false} onPress={() => setBuscando(true)} />}>
        {r.produtos.length ? (
          r.produtos.map((p) => <ItemProduto key={p.chave} p={p} r={r} f={f} />)
        ) : (
          <Texto variante="legenda" tom="fraco">
            Nenhum produto. Busque pelo nome, marca ou SKU.
          </Texto>
        )}
      </Bloco>

      <Linhas r={r} />

      <Bloco titulo={`Serviços (${r.servicos.length})`} direita={<Botao titulo="Adicionar" icone="add" tamanho="sm" larguraTotal={false} onPress={() => setServico(true)} />}>
        {r.servicos.length ? (
          r.servicos.map((s) => (
            <View key={s.chave} className="flex-row items-center gap-3 py-1">
              <View className="flex-1 gap-0.5">
                <Texto variante="corpoForte">{s.rotulo}</Texto>
                <Texto variante="legenda" tom="suave">
                  {s.descricao || '—'}
                  {s.portabilidade ? ` · portabilidade (${s.operadora_origem})` : ''}
                </Texto>
                {s.delta !== null ? (
                  <Texto variante="legenda" style={{ color: s.delta < 0 ? estado.perigo : estado.sucesso }}>
                    {reais(s.valor)}/mês · delta {reais(s.delta)}
                  </Texto>
                ) : null}
              </View>
              <BotaoIcone icone="trash-outline" rotulo={`Tirar ${s.rotulo}`} cor={estado.perigo} onPress={() => mudarRascunho((x) => ({ ...x, servicos: x.servicos.filter((o) => o.chave !== s.chave) }))} />
            </View>
          ))
        ) : (
          <Texto variante="legenda" tom="fraco">
            Alta, troca de plano, migração, seguro, SVA…
          </Texto>
        )}
      </Bloco>

      <BuscaProduto
        aberto={buscando}
        segmentacao={seg}
        aoFechar={() => setBuscando(false)}
        aoEscolher={(prod) => {
          mudarRascunho((x) => ({ ...x, produtos: [...x.produtos, novoProduto(prod, x.pagamentos, x.plano_anterior.segmentacao)] }));
          setBuscando(false);
        }}
      />
      <ServicoForm aberto={servico} r={r} f={f} aoFechar={() => setServico(false)} aoAdicionar={(s) => mudarRascunho((x) => ({ ...x, servicos: [...x.servicos, s] }))} />
    </View>
  );
}

function ItemProduto({ p, r, f }: { p: ProdutoRascunho; r: Rascunho; f: FormularioVenda }) {
  const cores = useCores();
  const renova = useConferirRenova();
  const aparelho = p.categoria_slv === 'aparelho';
  const c = calcItem(p, r.vivo_mais, f.vivo_mais);
  const mudar = (parcial: Partial<ProdutoRascunho>) => mudarRascunho((x) => ({ ...x, produtos: x.produtos.map((o) => (o.chave === p.chave ? { ...o, ...parcial } : o)) }));
  const repetido = !!p.imei && r.produtos.filter((o) => o.imei === p.imei).length > 1;
  const avisoImei = !aparelho
    ? null
    : !p.imei
      ? p.prateleira_infinita
        ? 'Prateleira infinita: o IMEI fica para depois.'
        : 'Obrigatório: 15 números (*#06# no aparelho ou na caixa).'
      : !imeiValido(p.imei)
        ? 'IMEI inválido — confira os 15 números.'
        : repetido
          ? 'Este IMEI já está em outro item da venda.'
          : null;
  const imeiOk = aparelho && imeiValido(p.imei) && !repetido;

  function conferirRenova() {
    if (!so(p.renova_codigo)) return mudar({ renova_ok: null, renova_info: null });
    renova.mutate(p.renova_codigo, {
      onSuccess: (j) =>
        mudar(
          j.ok
            ? { renova_ok: true, renova_codigo: j.codigo ?? p.renova_codigo, renova_info: [j.codigo, j.aparelho, j.ja_usado ? 'já usado em outra venda' : ''].filter(Boolean).join(' · ') }
            : { renova_ok: false, renova_info: j.erro ?? 'Código não encontrado.' },
        ),
      onError: () => mudar({ renova_ok: false, renova_info: 'Não deu para conferir o código agora.' }),
    });
  }

  return (
    <View className="gap-3 rounded-2xl p-3" style={{ borderWidth: 1, borderColor: cores.borda }}>
      <View className="flex-row items-start gap-2">
        <View className="flex-1 gap-1">
          <Texto variante="corpoForte">{p.nome}</Texto>
          <View className="flex-row flex-wrap gap-2">
            <Selo texto={p.categoria_slv} cor={marca[500]} ponto={false} />
            {p.sku ? <Selo texto={`SKU ${p.sku}`} cor={estado.info} ponto={false} /> : null}
          </View>
        </View>
        <BotaoIcone icone="trash-outline" rotulo={`Tirar ${p.nome}`} cor={estado.perigo} onPress={() => mudarRascunho((x) => ({ ...x, produtos: x.produtos.filter((o) => o.chave !== p.chave) }))} />
      </View>

      {p.skus.length > 1 ? (
        <Escolha
          rotulo="Cor / SKU"
          opcoes={p.skus.map((s) => ({ valor: s.sku, rotulo: s.cor ? `${s.cor} · ${s.sku}` : s.sku }))}
          valor={p.sku || null}
          aoMudar={(v) => mudar({ sku: v })}
        />
      ) : null}

      {p.tabela ? (
        <>
          <Escolha
            rotulo="Grupo do plano"
            opcoes={p.grupos.map((g) => ({ valor: g, rotulo: g }))}
            valor={p.grupo || null}
            aoMudar={(g) => mudarRascunho((x) => ({ ...x, produtos: x.produtos.map((o) => (o.chave === p.chave ? aplicarTabela({ ...o, grupo: g }, seg(x)) : o)) }))}
          />
          {p.grupo ? (
            <View className="flex-row flex-wrap gap-2">
              {(p.condicoes[p.grupo] ?? [COND_BASE]).map((cond) => {
                const v = p.tabela?.[p.grupo]?.[cond];
                return (
                  <Chip
                    key={cond}
                    rotulo={`${rotuloCondicao(cond)}${v ? ` · ${reais(v)}` : ''}`}
                    ativo={p.condicao === cond}
                    onPress={() => mudarRascunho((x) => ({ ...x, produtos: x.produtos.map((o) => (o.chave === p.chave ? aplicarTabela({ ...o, condicao: cond }, seg(x)) : o)) }))}
                  />
                );
              })}
            </View>
          ) : null}
        </>
      ) : null}

      {p.sem_valor ? (
        <CampoDinheiro rotulo="Valor do produto *" valor={p.valor_informado} aoMudar={(v) => mudar({ valor_informado: v, valor_final: '' })} ajuda={`Sem valor na tabela (${p.regra}).`} />
      ) : (
        <Texto variante="legenda" tom="fraco">
          Tabela: {reais(p.valor_sugerido)} · {p.regra}
        </Texto>
      )}

      {aparelho || p.exige_serial ? (
        <Campo
          rotulo={aparelho ? 'IMEI *' : 'Serial *'}
          icone="barcode-outline"
          value={p.imei}
          onChangeText={(v) => mudar({ imei: aparelho ? so(v).slice(0, 15) : v.trim() })}
          keyboardType={aparelho ? 'number-pad' : 'default'}
          autoCapitalize="characters"
          maxLength={aparelho ? 15 : 60}
          placeholder={aparelho ? '15 números' : 'Na caixa ou na etiqueta'}
          erro={aparelho && p.imei && avisoImei ? avisoImei : null}
          ajuda={imeiOk ? 'IMEI ok' : (avisoImei ?? undefined)}
        />
      ) : (
        <Campo rotulo="Serial (opcional)" value={p.imei} onChangeText={(v) => mudar({ imei: v.trim() })} maxLength={60} />
      )}
      {aparelho || p.exige_serial ? (
        <Interruptor rotulo="Prateleira infinita" detalhe="O produto vem depois: o IMEI/serial fica para depois." valor={p.prateleira_infinita} aoMudar={(v) => mudar({ prateleira_infinita: v })} />
      ) : null}

      {aparelho ? (
        <View className="gap-2">
          <Interruptor
            rotulo="Houve Renova?"
            valor={p.renova}
            aoMudar={(v) => mudar(v ? { renova: true } : { renova: false, renova_vini: false, renova_alied: false, renova_assurant: false, valor_final: '' })}
          />
          {p.renova ? (
            <View className="gap-3 rounded-xl p-3" style={{ backgroundColor: cores.superficie2 }}>
              <Interruptor rotulo="Renova Vini" valor={p.renova_vini} aoMudar={(v) => mudar({ renova_vini: v, valor_final: '' })} />
              {p.renova_vini ? (
                <>
                  <Campo
                    rotulo="Código do Renova *"
                    value={p.renova_codigo}
                    onChangeText={(v) => mudar({ renova_codigo: v.toUpperCase(), renova_ok: null, renova_info: null })}
                    onEndEditing={conferirRenova}
                    autoCapitalize="characters"
                    placeholder="RN-000000"
                    erro={p.renova_ok === false ? p.renova_info : null}
                    ajuda={renova.isPending ? 'Conferindo…' : p.renova_ok ? p.renova_info ?? undefined : 'O checklist do Renova no portal.'}
                  />
                  <CampoDinheiro rotulo="Valor do Renova Vini *" valor={p.renova_vini_valor} aoMudar={(v) => mudar({ renova_vini_valor: v, valor_final: '' })} />
                </>
              ) : null}
              <Interruptor rotulo="Renova Alied" valor={p.renova_alied} aoMudar={(v) => mudar({ renova_alied: v, valor_final: '' })} />
              {p.renova_alied ? (
                <>
                  <CampoDinheiro rotulo="Valor do Renova Alied *" valor={p.renova_alied_valor} aoMudar={(v) => mudar({ renova_alied_valor: v, valor_final: '' })} />
                  <Campo rotulo="Pedido do Renova Alied *" value={p.renova_alied_pedido} onChangeText={(v) => mudar({ renova_alied_pedido: v.toUpperCase() })} autoCapitalize="characters" maxLength={40} />
                </>
              ) : null}
              <Interruptor rotulo="Renova Assurant" valor={p.renova_assurant} aoMudar={(v) => mudar({ renova_assurant: v, valor_final: '' })} />
              {p.renova_assurant ? (
                <>
                  <CampoDinheiro rotulo="Valor do Renova Assurant *" valor={p.renova_assurant_valor} aoMudar={(v) => mudar({ renova_assurant_valor: v, valor_final: '' })} />
                  <Campo rotulo="Pedido do Renova Assurant" value={p.renova_assurant_pedido} onChangeText={(v) => mudar({ renova_assurant_pedido: v.toUpperCase() })} autoCapitalize="characters" maxLength={40} />
                </>
              ) : null}
            </View>
          ) : null}
        </View>
      ) : null}

      <View className="rounded-xl px-3 py-1" style={{ backgroundColor: cores.superficie2 }}>
        <LinhaValor rotulo="Valor do produto" valor={reais(c.sug)} />
        {c.rv ? <LinhaValor rotulo="Renova Vini" valor={`− ${reais(c.rv)}`} cor={estado.sucesso} /> : null}
        {c.ra ? <LinhaValor rotulo="Renova Alied" valor={`− ${reais(c.ra)}`} cor={estado.sucesso} /> : null}
        {c.ras ? <LinhaValor rotulo="Renova Assurant" valor={`− ${reais(c.ras)}`} cor={estado.sucesso} /> : null}
        {c.desc ? <LinhaValor rotulo={`Vivo+ (${f.vivo_mais}%)`} valor={`− ${reais(c.desc)}`} cor={estado.sucesso} /> : null}
        <LinhaValor rotulo={c.editado ? 'Valor final (editado)' : 'Valor final'} valor={reais(c.final)} destaque cor={c.final <= 0 ? estado.perigo : c.editado ? estado.alerta : undefined} />
      </View>
      <CampoDinheiro
        rotulo="Editar valor final (opcional)"
        valor={p.valor_final}
        aoMudar={(v) => mudar({ valor_final: v })}
        placeholder={reais(c.calc)}
        ajuda={c.editado ? `Calculado: ${reais(c.calc)} — apague para voltar.` : 'Deixe vazio para usar o calculado.'}
      />
    </View>
  );
}

const seg = (x: Rascunho) => x.plano_anterior.segmentacao;

function Linhas({ r }: { r: Rascunho }) {
  const cores = useCores();
  const [nova, setNova] = useState('');
  const d = so(nova);
  const valida = !!d && (telValido(d) || fake(d));
  return (
    <Bloco titulo="Linhas do cliente">
      <Texto variante="legenda" tom="fraco">
        A linha (número) em que os serviços são feitos. Número fictício marca a venda para conferência.
      </Texto>
      {r.linhas.length ? (
        <View className="flex-row flex-wrap gap-2">
          {r.linhas.map((l) => (
            <View key={l} className="flex-row items-center gap-1.5 rounded-full px-3 py-1.5" style={{ backgroundColor: fake(l) ? `${estado.alerta}22` : cores.superficie2 }}>
              <Icone nome={fake(l) ? 'call-outline' : 'call'} tamanho={14} cor={fake(l) ? estado.alerta : cores.texto3} />
              <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito }}>
                {mascaraTel(l)}
                {fake(l) ? ' · fictício' : ''}
              </Texto>
              <BotaoIcone icone="close" rotulo={`Tirar a linha ${l}`} onPress={() => mudarRascunho((x) => ({ ...x, linhas: x.linhas.filter((o) => o !== l) }))} />
            </View>
          ))}
        </View>
      ) : null}
      <View className="flex-row items-end gap-2">
        <View style={{ flex: 1 }}>
          <Campo rotulo="Adicionar linha" value={nova} onChangeText={(v) => setNova(mascaraTel(v))} keyboardType="phone-pad" placeholder="(27) 99999-0000" maxLength={16} erro={d.length >= 10 && !valida ? 'Use DDD + número.' : null} />
        </View>
        <Botao
          titulo="Pôr"
          tamanho="md"
          larguraTotal={false}
          desativado={!valida}
          onPress={() => {
            mudarRascunho((x) => ({ ...x, linhas: x.linhas.includes(d) ? x.linhas : [...x.linhas, d] }));
            setNova('');
          }}
        />
      </View>
    </Bloco>
  );
}
