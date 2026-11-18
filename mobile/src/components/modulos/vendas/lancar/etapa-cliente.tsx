/** Etapa 1: PDV/vendedor, cliente pelo CPF/CNPJ (com pré-análise) e o plano que ele já tem. */
import { useEffect, useRef } from 'react';
import { ActivityIndicator, View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Botao, Campo, Icone, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { marca } from '@/constants/theme';
import { useBuscarClienteVenda } from '@/hooks/modulos/vendas';
import { lancar, type FormularioVenda } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

import { Bloco, CampoDinheiro, Escolha } from './pecas';
import {
  aplicarTabela,
  CLIENTE_VAZIO,
  cnpjValido,
  cpfValido,
  type DadosCliente,
  mascaraDoc,
  mascaraTel,
  moeda,
  mudarRascunho,
  num,
  type Rascunho,
  so,
} from './rascunho';

export function EtapaCliente({ r, f }: { r: Rascunho; f: FormularioVenda }) {
  const busca = useBuscarClienteVenda();
  const { mutate: buscar } = busca;
  const ultimo = useRef(so(r.cpf));
  const d = so(r.cpf);
  const valido = d.length === 14 ? cnpjValido(d) : d.length === 11 ? cpfValido(d) : false;

  // CPF/CNPJ completo e válido → busca (com 11 números pode ser um CNPJ sendo digitado: espera um instante).
  useEffect(() => {
    if (!valido || d === ultimo.current) return;
    const t = setTimeout(
      () => {
        ultimo.current = d;
        buscar(d, {
          onSuccess: (j) => {
            if (!j.ok) return;
            if (j.encontrado) {
              const c = j.cliente;
              mudarRascunho((x) => ({
                ...x,
                cpf_formatado: c.cpf_formatado,
                encontrado: true,
                confirmado: false,
                origem: [c.origem === 'VIVOGO' ? 'Base do Vivo GO' : 'Cadastro do portal', c.ultima_compra ? `última compra ${c.ultima_compra}${c.pdv_ultimo ? ` em ${c.pdv_ultimo}` : ''}` : '', c.qtd_vendas ? `${c.qtd_vendas} venda(s)` : '']
                  .filter(Boolean)
                  .join(' · '),
                pre: j.pre_analise,
                cliente: {
                  nome: c.nome,
                  telefone: mascaraTel(c.telefone || ''),
                  cep: c.cep,
                  logradouro: c.logradouro,
                  numero: c.numero,
                  complemento: c.complemento,
                  bairro: c.bairro,
                  cidade: c.cidade,
                  uf: c.uf,
                },
                plano_anterior: { id: c.plano_id, segmentacao: c.segmentacao || '', valor: c.valor_pago ? moeda(num(c.valor_pago)) : '' },
                linhas: c.telefone ? [so(c.telefone)] : [],
              }));
            } else {
              mudarRascunho((x) => ({ ...x, cpf_formatado: j.cpf_formatado, encontrado: false, confirmado: true, origem: '', pre: null, cliente: CLIENTE_VAZIO, linhas: [] }));
            }
          },
        });
      },
      d.length === 11 ? 700 : 0,
    );
    return () => clearTimeout(t);
  }, [d, valido, buscar]);

  function trocarDoc(v: string) {
    const novo = so(v);
    mudarRascunho((x) =>
      novo === so(x.cpf) ? { ...x, cpf: mascaraDoc(v) } : { ...x, cpf: mascaraDoc(v), encontrado: null, confirmado: false, pre: null, cliente: CLIENTE_VAZIO, origem: '' },
    );
    if (novo !== ultimo.current) ultimo.current = '';
  }

  const campo = (k: keyof DadosCliente) => (v: string) =>
    mudarRascunho((x) => ({ ...x, cliente: { ...x.cliente, [k]: k === 'telefone' ? mascaraTel(v) : k === 'uf' ? v.toUpperCase().slice(0, 2) : v } }));

  const erroDoc = d.length >= 11 && d.length !== 12 && d.length !== 13 && !valido ? `${d.length === 14 ? 'CNPJ' : 'CPF'} inválido — confira os números.` : busca.data && !busca.data.ok ? busca.data.erro : null;

  return (
    <View className="gap-4">
      <PdvVendedor r={r} f={f} />

      <Bloco titulo="Cliente">
        <Campo
          rotulo="CPF ou CNPJ"
          icone="card-outline"
          value={r.cpf}
          onChangeText={trocarDoc}
          keyboardType="number-pad"
          placeholder="000.000.000-00"
          maxLength={18}
          erro={erroDoc}
          ajuda={busca.isPending ? 'Buscando o cliente…' : busca.isError ? 'Não deu para buscar agora — apague um número e digite de novo.' : undefined}
        />
        {busca.isPending ? <ActivityIndicator color={marca[500]} /> : null}

        {r.encontrado === true && !r.confirmado ? (
          <View className="gap-3 rounded-xl p-3" style={{ backgroundColor: `${marca[500]}12` }}>
            <Texto variante="titulo3">{r.cliente.nome}</Texto>
            <Texto variante="legenda" tom="suave">
              {r.origem}
            </Texto>
            <View className="flex-row gap-3">
              <Botao titulo="É ele" icone="checkmark" tamanho="sm" style={{ flex: 1 }} onPress={() => mudarRascunho((x) => ({ ...x, confirmado: true }))} />
              <Botao
                titulo="Não é"
                variante="secundario"
                tamanho="sm"
                style={{ flex: 1 }}
                onPress={() => {
                  ultimo.current = '';
                  mudarRascunho((x) => ({ ...x, cpf: '', encontrado: null, confirmado: false, pre: null, cliente: CLIENTE_VAZIO }));
                }}
              />
            </View>
          </View>
        ) : null}

        {r.confirmado ? (
          <View className="gap-3">
            {r.encontrado === false ? (
              <View className="flex-row items-center gap-2">
                <Icone nome="person-add-outline" tamanho={18} cor={marca[600]} />
                <Texto variante="legenda" tom="marca" className="flex-1">
                  Cliente novo: o cadastro é gravado junto com a venda.
                </Texto>
              </View>
            ) : (
              <Texto variante="legenda" tom="fraco">
                Telefone e endereço: edite se mudou (o anterior fica no histórico).
              </Texto>
            )}
            <Campo rotulo="Nome *" value={r.cliente.nome} onChangeText={campo('nome')} editable={r.encontrado === false} autoCapitalize="words" maxLength={200} />
            <Campo rotulo="Telefone" icone="call-outline" value={r.cliente.telefone} onChangeText={campo('telefone')} keyboardType="phone-pad" placeholder="(27) 99999-0000" maxLength={16} />
            <View className="flex-row gap-3">
              <View style={{ flex: 1 }}>
                <Campo rotulo="CEP" value={r.cliente.cep} onChangeText={campo('cep')} keyboardType="number-pad" maxLength={9} />
              </View>
              <View style={{ width: 90 }}>
                <Campo rotulo="UF" value={r.cliente.uf} onChangeText={campo('uf')} autoCapitalize="characters" maxLength={2} />
              </View>
            </View>
            <Campo rotulo="Logradouro" value={r.cliente.logradouro} onChangeText={campo('logradouro')} />
            <View className="flex-row gap-3">
              <View style={{ width: 110 }}>
                <Campo rotulo="Número" value={r.cliente.numero} onChangeText={campo('numero')} />
              </View>
              <View style={{ flex: 1 }}>
                <Campo rotulo="Complemento" value={r.cliente.complemento} onChangeText={campo('complemento')} />
              </View>
            </View>
            <Campo rotulo="Bairro" value={r.cliente.bairro} onChangeText={campo('bairro')} />
            <Campo rotulo="Cidade" value={r.cliente.cidade} onChangeText={campo('cidade')} />
          </View>
        ) : null}
      </Bloco>

      {r.confirmado && r.pre ? <PreAnalise r={r} /> : null}
      {r.confirmado ? <PlanoAnterior r={r} f={f} /> : null}
    </View>
  );
}

function PdvVendedor({ r, f }: { r: Rascunho; f: FormularioVenda }) {
  if (!f.pode_outros) {
    return (
      <Bloco titulo="PDV e vendedor">
        <LinhaValor rotulo="PDV (do seu cadastro)" valor={f.loja?.nome ?? '—'} destaque />
        <LinhaValor rotulo="Vendedor" valor={f.eu.nome} />
      </Bloco>
    );
  }
  return (
    <Bloco titulo="PDV e vendedor">
      <Seletor
        rotulo="PDV"
        placeholder={f.loja?.nome ?? 'Escolha o PDV'}
        itens={f.lojas.map((l) => ({ id: l.id, nome: l.nome }))}
        valor={r.loja_id ?? f.loja?.id ?? null}
        aoEscolher={(id) => mudarRascunho((x) => ({ ...x, loja_id: id }))}
      />
      <Seletor
        rotulo="Vendedor"
        placeholder={`${f.eu.nome} (eu)`}
        itens={[{ id: f.eu.id, nome: `${f.eu.nome} (eu)` }, ...f.vendedores.filter((v) => v.id !== f.eu.id).map((v) => ({ id: v.id, nome: v.nome, detalhe: v.detalhe ?? undefined }))]}
        valor={r.vendedor_id ?? f.eu.id}
        aoEscolher={(id) => mudarRascunho((x) => ({ ...x, vendedor_id: id === f.eu.id ? null : id }))}
      />
    </Bloco>
  );
}

function PreAnalise({ r }: { r: Rascunho }) {
  const p = r.pre;
  if (!p) return null;
  return (
    <Bloco titulo="Pré-análise">
      {p.plano ? (
        <View>
          <Texto variante="corpoForte">{p.plano.nome}</Texto>
          <Texto variante="legenda" tom="suave">
            {[p.plano.segmentacao_nome, `paga ${reais(p.plano.valor)}`, p.plano.fonte].filter(Boolean).join(' · ')}
          </Texto>
        </View>
      ) : (
        <Texto variante="legenda" tom="fraco">
          Sem plano registrado no portal.
        </Texto>
      )}
      {p.compras.length ? (
        <View>
          <Texto variante="rotulo" tom="fraco">
            Últimas compras
          </Texto>
          {p.compras.map((c, i) => (
            <LinhaValor key={i} rotulo={`${c.data} · ${c.produto}${c.pdv ? ` (${c.pdv})` : ''}`} valor={reais(c.valor)} />
          ))}
        </View>
      ) : (
        <Texto variante="legenda" tom="fraco">
          {p.vazio ? 'Cliente sem histórico de planos ou compras.' : 'Sem compras de produto no portal.'}
        </Texto>
      )}
    </Bloco>
  );
}

function PlanoAnterior({ r, f }: { r: Rascunho; f: FormularioVenda }) {
  const seg = r.plano_anterior.segmentacao;

  // Mudou o plano do cliente: o preço sugerido dos produtos muda junto (a web faz o mesmo).
  function reprecificar(novaSeg: string) {
    mudarRascunho((x) => ({ ...x, produtos: x.produtos.map((p) => (p.tabela ? aplicarTabela({ ...p, grupo: '' }, novaSeg) : p)) }));
    const ids = r.produtos.filter((p) => !p.tabela).map((p) => p.preco_id);
    if (!ids.length) return;
    void lancar
      .produtosPorId(ids, novaSeg)
      .then((lista) =>
        mudarRascunho((x) => ({
          ...x,
          produtos: x.produtos.map((p) => {
            const n = lista.find((l) => l.id === p.preco_id);
            return n && !p.tabela ? { ...p, valor_sugerido: num(n.valor_sugerido), sem_valor: !n.valor_sugerido, regra: n.regra } : p;
          }),
        })),
      )
      .catch(() => undefined);
  }

  return (
    <Bloco titulo="Plano atual do cliente">
      <Escolha
        rotulo="Segmentação"
        opcoes={[{ valor: '', rotulo: 'Sem plano' }, ...f.segmentacoes]}
        valor={seg}
        aoMudar={(v) => {
          mudarRascunho((x) => ({ ...x, plano_anterior: { id: null, segmentacao: v, valor: '' } }));
          reprecificar(v);
        }}
      />
      {seg ? (
        <Seletor
          rotulo="Plano"
          placeholder="Escolha o plano…"
          itens={f.planos.filter((p) => p.segmentacao === seg).map((p) => ({ id: p.id, nome: p.nome, detalhe: reais(p.valor) }))}
          valor={r.plano_anterior.id}
          aoEscolher={(id) => {
            const p = f.planos.find((x) => x.id === id);
            mudarRascunho((x) => ({ ...x, plano_anterior: { id, segmentacao: p?.segmentacao ?? seg, valor: p ? moeda(p.valor) : x.plano_anterior.valor } }));
          }}
        />
      ) : null}
      {seg ? (
        <CampoDinheiro
          rotulo="Quanto paga hoje"
          valor={r.plano_anterior.valor}
          aoMudar={(v) => mudarRascunho((x) => ({ ...x, plano_anterior: { ...x.plano_anterior, valor: v } }))}
        />
      ) : null}
      <Texto variante="legenda" tom="fraco">
        O plano define o preço dos aparelhos (Pré ou sem plano: grupo PRÉ da tabela).
      </Texto>
    </Bloco>
  );
}
