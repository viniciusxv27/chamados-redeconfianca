/** Folha para adicionar um serviço: os campos de cada tipo, como na tela da web (venda_form.html:912-1000). */
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Modal, Platform, ScrollView, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BotaoGradiente, BotaoIcone, Campo, Cartao, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { usePlanosFora } from '@/hooks/modulos/vendas';
import { useCores } from '@/providers/tema';
import type { FormularioVenda, PlanoFora } from '@/services/modulos/vendas';
import { reais } from '@/utils/dinheiro';

import { CaixaErros, Escolha, Interruptor } from './pecas';
import { cpfValido, mascaraDoc, mascaraTel, novaChave, num, type Rascunho, type ServicoRascunho, so, telValido } from './rascunho';

type Campos = {
  tipo: string;
  segNovo: string;
  plano_id: number | null;
  segAnt: string;
  plano_anterior_id: number | null;
  sem_plano: boolean;
  fora: PlanoFora | null;
  portabilidade: boolean;
  operadora_origem: string;
  serial: string;
  servico_adicional_id: number | null;
  novo_titular_cpf: string;
  novo_titular_nome: string;
  numero_novo: string;
  numero_ordem: string;
};

export function ServicoForm({ aberto, r, f, aoFechar, aoAdicionar }: { aberto: boolean; r: Rascunho; f: FormularioVenda; aoFechar: () => void; aoAdicionar: (s: ServicoRascunho) => void }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const vazio: Campos = {
    tipo: '',
    segNovo: '',
    plano_id: null,
    segAnt: r.plano_anterior.segmentacao,
    plano_anterior_id: r.plano_anterior.id,
    sem_plano: false,
    fora: null,
    portabilidade: false,
    operadora_origem: '',
    serial: '',
    servico_adicional_id: null,
    novo_titular_cpf: '',
    novo_titular_nome: '',
    numero_novo: '',
    numero_ordem: '',
  };
  const [c, setC] = useState<Campos>(vazio);
  const [erros, setErros] = useState<string[]>([]);
  const [buscaFora, setBuscaFora] = useState('');
  const fora = usePlanosFora(buscaFora.trim());
  const tipo = f.tipos_servico.find((t) => t.valor === c.tipo);
  const campos = tipo?.campos ?? {};
  const mudar = (parcial: Partial<Campos>) => setC((x) => ({ ...x, ...parcial }));
  const segsDe = (lista?: string[]) => f.segmentacoes.filter((s) => lista?.includes(s.valor));
  const planosDe = (seg: string) => f.planos.filter((p) => p.segmentacao === seg).map((p) => ({ id: p.id, nome: p.nome, detalhe: reais(p.valor) }));

  function fechar() {
    setC(vazio);
    setErros([]);
    setBuscaFora('');
    aoFechar();
  }

  function adicionar() {
    const e: string[] = [];
    const t = c.tipo;
    if (!t) e.push('Escolha o tipo de serviço.');
    if (campos.plano && !c.plano_id) e.push('Escolha a segmentação e o plano.');
    if (t === 'ALTA' && c.portabilidade && !c.operadora_origem) e.push('É portabilidade: escolha a operadora de origem.');
    if (c.segNovo === 'FIBRA' && !c.numero_ordem.replace(/\s+/g, '')) e.push('Venda de fibra: informe o número da ordem.');
    if (t === 'TROCA_SIMCARD' && !/^89\d{17,18}$/.test(so(c.serial))) e.push('Serial do SimCard: 19 ou 20 números começando por 89.');
    if (campos.adicional && !c.servico_adicional_id) e.push('Escolha o serviço contratado.');
    if (t === 'TROCA_TITULARIDADE' && !cpfValido(c.novo_titular_cpf)) e.push('CPF do novo titular inválido.');
    if (t === 'TROCA_TITULARIDADE' && !c.novo_titular_nome.trim()) e.push('Informe o nome do novo titular.');
    if (t === 'TROCA_NUMERO' && !telValido(c.numero_novo)) e.push('Número novo: DDD + número.');
    if (!r.linhas.length) e.push('Informe a linha do cliente (em Linhas do cliente, antes de adicionar).');
    setErros(e);
    if (e.length) return;
    if (campos.adicional) {
      // Seguro e SVA só entram depois de ativados (a web pergunta do mesmo jeito).
      Alert.alert(`O ${tipo?.rotulo.toLowerCase()} foi ativado?`, 'Só confirme se o serviço já está ativo para o cliente.', [
        { text: 'Ainda não', style: 'cancel' },
        { text: 'Sim, está ativo', onPress: () => concluir(true) },
      ]);
      return;
    }
    concluir(false);
  }

  function concluir(ativado: boolean) {
    const plano = f.planos.find((p) => p.id === c.plano_id) ?? null;
    const adicional = f.adicionais.find((a) => a.id === c.servico_adicional_id) ?? null;
    const anterior = c.sem_plano
      ? 0
      : c.fora
        ? num(c.fora.valor)
        : (f.planos.find((p) => p.id === c.plano_anterior_id)?.valor ?? num(r.plano_anterior.valor));
    const valor = plano?.valor ?? adicional?.valor ?? 0;
    const s: ServicoRascunho = {
      chave: novaChave(),
      tipo: c.tipo,
      rotulo: tipo?.rotulo ?? c.tipo,
      descricao: [plano?.nome ?? adicional?.nome, c.fora ? `antes: ${c.fora.nome}` : c.sem_plano ? 'cliente sem plano' : null].filter(Boolean).join(' · '),
      valor,
      delta: plano ? Math.round((valor - (anterior ?? 0)) * 100) / 100 : null,
      plano_id: c.plano_id,
      plano_anterior_id: campos.plano_anterior && !c.sem_plano ? c.plano_anterior_id : null,
      plano_fora_id: c.sem_plano ? null : (c.fora?.id ?? null),
      plano_fora: c.fora,
      sem_plano: c.sem_plano,
      serial: so(c.serial),
      servico_adicional_id: c.servico_adicional_id,
      ativacao_confirmada: ativado,
      novo_titular_cpf: so(c.novo_titular_cpf),
      novo_titular_nome: c.novo_titular_nome.trim(),
      numero_novo: so(c.numero_novo),
      numero_ordem: c.segNovo === 'FIBRA' ? c.numero_ordem.replace(/\s+/g, '') : '',
      portabilidade: c.portabilidade,
      operadora_origem: c.portabilidade ? c.operadora_origem : '',
    };
    aoAdicionar(s);
    fechar();
  }

  const adicionais = f.adicionais.filter((a) => a.tipo === c.tipo);

  return (
    <Modal visible={aberto} animationType="slide" presentationStyle="pageSheet" onRequestClose={fechar}>
      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ backgroundColor: cores.fundo }}>
        <View className="flex-row items-center gap-3 px-5 pb-3" style={{ paddingTop: Platform.OS === 'ios' ? 16 : insets.top + 12 }}>
          <Texto variante="titulo2" className="flex-1">
            Adicionar serviço
          </Texto>
          <BotaoIcone icone="close" rotulo="Fechar" onPress={fechar} />
        </View>
        <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 4, gap: 16, paddingBottom: insets.bottom + 40 }} keyboardShouldPersistTaps="handled">
          <Escolha rotulo="Tipo de serviço" opcoes={f.tipos_servico} valor={c.tipo || null} aoMudar={(t) => setC({ ...vazio, tipo: t })} />

          {campos.sem_plano ? (
            <Interruptor rotulo="Cliente sem plano" detalhe="Não tinha plano antes: o delta é o valor inteiro do plano novo." valor={c.sem_plano} aoMudar={(v) => mudar({ sem_plano: v, fora: null })} />
          ) : null}

          {campos.plano_anterior && !c.sem_plano ? (
            <Cartao style={{ padding: 14, gap: 10 }}>
              <Escolha rotulo="Plano anterior (confirme ou corrija)" opcoes={segsDe(campos.plano_anterior)} valor={c.segAnt || null} aoMudar={(v) => mudar({ segAnt: v, plano_anterior_id: null })} />
              {c.segAnt ? (
                <Seletor rotulo="Plano anterior" placeholder="Escolha o plano…" itens={planosDe(c.segAnt)} valor={c.plano_anterior_id} aoEscolher={(id) => mudar({ plano_anterior_id: id })} />
              ) : null}
            </Cartao>
          ) : null}

          {campos.plano_fora && !c.sem_plano ? (
            <Cartao style={{ padding: 14, gap: 10 }}>
              <Texto variante="corpoForte">Plano anterior fora do portfólio</Texto>
              {c.fora ? (
                <View className="flex-row items-center gap-2">
                  <Texto variante="legenda" className="flex-1">
                    {c.fora.nome} · {reais(c.fora.valor)}
                  </Texto>
                  <BotaoIcone icone="close" rotulo="Tirar plano fora do portfólio" onPress={() => mudar({ fora: null })} />
                </View>
              ) : (
                <>
                  <Campo icone="search" placeholder="Nome ou valor do plano antigo" value={buscaFora} onChangeText={setBuscaFora} autoCorrect={false} ajuda="Só quando o plano do cliente não está na lista de planos." />
                  {(fora.data ?? []).slice(0, 8).map((p) => (
                    <Cartao key={p.id} onPress={() => mudar({ fora: p })} style={{ padding: 12 }} acessibilidade={`Escolher ${p.nome}`}>
                      <Texto variante="legenda">
                        {p.nome} · {reais(p.valor)}
                        {p.segmentacao ? ` · ${p.segmentacao}` : ''}
                      </Texto>
                    </Cartao>
                  ))}
                </>
              )}
            </Cartao>
          ) : null}

          {campos.plano ? (
            <Cartao style={{ padding: 14, gap: 10 }}>
              <Escolha
                rotulo={c.tipo === 'TROCA_TITULARIDADE' ? 'Segmentação e plano do novo titular' : campos.plano_anterior ? 'Novo plano' : 'Segmentação e plano'}
                opcoes={segsDe(campos.plano)}
                valor={c.segNovo || null}
                aoMudar={(v) => mudar({ segNovo: v, plano_id: null, numero_ordem: '' })}
              />
              {c.segNovo ? <Seletor rotulo="Plano" placeholder="Escolha o plano…" itens={planosDe(c.segNovo)} valor={c.plano_id} aoEscolher={(id) => mudar({ plano_id: id })} /> : null}
              {c.segNovo === 'FIBRA' ? (
                <Campo rotulo="Número da ordem (fibra) *" value={c.numero_ordem} onChangeText={(v) => mudar({ numero_ordem: v })} maxLength={40} autoCapitalize="characters" placeholder="Ex.: 8037382808" />
              ) : null}
            </Cartao>
          ) : null}

          {campos.portabilidade ? (
            <Cartao style={{ padding: 14, gap: 10 }}>
              <Interruptor rotulo="É portabilidade?" valor={c.portabilidade} aoMudar={(v) => mudar({ portabilidade: v, operadora_origem: '' })} />
              {c.portabilidade ? <Escolha rotulo="Operadora de origem" opcoes={f.operadoras} valor={c.operadora_origem || null} aoMudar={(v) => mudar({ operadora_origem: v })} /> : null}
            </Cartao>
          ) : null}

          {campos.serial ? (
            <Campo rotulo="Serial do SimCard (ICCID) *" value={c.serial} onChangeText={(v) => mudar({ serial: so(v).slice(0, 20) })} keyboardType="number-pad" placeholder="8955…" ajuda="19 ou 20 números, começando por 89." />
          ) : null}

          {campos.adicional ? (
            adicionais.length ? (
              <Seletor
                rotulo={`${tipo?.rotulo} contratado`}
                placeholder="Escolha…"
                itens={adicionais.map((a) => ({ id: a.id, nome: a.nome, detalhe: a.valor ? reais(a.valor) : undefined }))}
                valor={c.servico_adicional_id}
                aoEscolher={(id) => mudar({ servico_adicional_id: id })}
              />
            ) : (
              <Texto variante="legenda" tom="perigo">
                Nenhum {tipo?.rotulo.toLowerCase()} cadastrado. O administrador cadastra em Parametrização.
              </Texto>
            )
          ) : null}

          {campos.titular ? (
            <>
              <Campo rotulo="CPF do novo titular *" value={c.novo_titular_cpf} onChangeText={(v) => mudar({ novo_titular_cpf: mascaraDoc(v).slice(0, 14) })} keyboardType="number-pad" maxLength={14} />
              <Campo rotulo="Nome do novo titular *" value={c.novo_titular_nome} onChangeText={(v) => mudar({ novo_titular_nome: v })} autoCapitalize="words" maxLength={200} />
              <Texto variante="legenda" tom="fraco">
                Titular atual: {r.cliente.nome || '—'}
              </Texto>
            </>
          ) : null}

          {campos.numero_novo ? (
            <>
              <Texto variante="legenda" tom="fraco">
                Número atual: {r.linhas[0] ? mascaraTel(r.linhas[0]) : '—'}
              </Texto>
              <Campo rotulo="Número novo *" value={c.numero_novo} onChangeText={(v) => mudar({ numero_novo: mascaraTel(v) })} keyboardType="phone-pad" maxLength={16} />
            </>
          ) : null}

          <CaixaErros erros={erros} />
          {c.tipo ? <BotaoGradiente titulo="Adicionar serviço" icone="add" onPress={adicionar} /> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </Modal>
  );
}
