"""Traz do dados.db do sistema antigo as contas bancárias, as categorias e a regra do Agging.

    venv/bin/python manage.py importar_contas_pagar_local /caminho/para/dados.db [--simular]

Idempotente: rodar de novo atualiza o que já existe e não duplica nada.
- Conta bancária: casa pelo (banco, agência, conta); atualiza ordem e "ativa".
- Categoria: casa pelo tipo; atualiza a categoria.
- Regra do Agging: as chaves agging_despesa_contem / agging_fornecedor_contem.
Rascunhos não publicados do sistema antigo não são trazidos.
As contas não ficam no repositório: só entram por aqui.
"""
import os
import sqlite3

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from contas_pagar.models import Categoria, ConfiguracaoContasPagar, ContaBancaria


class Command(BaseCommand):
    help = 'Importa contas bancárias, categorias e regras do dados.db do Contas a Pagar antigo.'

    def add_arguments(self, parser):
        parser.add_argument('caminho', help='Caminho do dados.db (SQLite) do sistema antigo')
        parser.add_argument('--simular', action='store_true', help='Só mostra o que faria (desfaz no fim)')

    def handle(self, caminho, simular=False, **opts):
        if not os.path.isfile(caminho):
            raise CommandError(f'Arquivo não encontrado: {caminho}')
        try:
            con = sqlite3.connect(f'file:{caminho}?mode=ro', uri=True)
            con.row_factory = sqlite3.Row
            tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            raise CommandError(f'Não é um SQLite válido: {exc}') from exc
        if not {'bancos', 'categorias'} <= tabelas:
            raise CommandError('O arquivo não tem as tabelas bancos/categorias do Contas a Pagar.')
        bancos = [dict(r) for r in con.execute('SELECT * FROM bancos ORDER BY ordem, id')]
        cats = [(r['tipo'], r['categoria']) for r in con.execute('SELECT tipo, categoria FROM categorias')]
        cfg = {r['chave']: r['valor'] for r in con.execute('SELECT chave, valor FROM config')} if 'config' in tabelas else {}
        con.close()

        n = {'contas_novas': 0, 'contas_atualizadas': 0, 'cats_novas': 0, 'cats_atualizadas': 0}
        with transaction.atomic():
            for b in bancos:
                chave = {'banco': (b.get('banco') or '').strip().upper()[:200],
                         'agencia': (b.get('agencia') or '').strip()[:20], 'conta': (b.get('conta') or '').strip()[:30]}
                if not chave['banco']:
                    continue
                dados = {'ordem': int(b.get('ordem') or 0), 'ativo': bool(b.get('ativo', 1))}
                obj = ContaBancaria.objects.filter(**chave).order_by('id').first()
                if obj is None:
                    ContaBancaria.objects.create(**chave, **dados)
                    n['contas_novas'] += 1
                elif any(getattr(obj, k) != v for k, v in dados.items()):
                    ContaBancaria.objects.filter(pk=obj.pk).update(**dados)
                    n['contas_atualizadas'] += 1
            for tipo, cat in cats:
                tipo, cat = (tipo or '').strip()[:255], (cat or '').strip()[:120]
                if not tipo or not cat:
                    continue
                obj = Categoria.objects.filter(tipo=tipo).first()
                if obj is None:
                    Categoria.objects.create(tipo=tipo, categoria=cat)
                    n['cats_novas'] += 1
                elif obj.categoria != cat:
                    Categoria.objects.filter(pk=obj.pk).update(categoria=cat)
                    n['cats_atualizadas'] += 1
            c = ConfiguracaoContasPagar.atual()
            mudou = False
            for k in ('agging_despesa_contem', 'agging_fornecedor_contem'):
                if k in cfg and cfg[k] is not None and getattr(c, k) != cfg[k]:
                    setattr(c, k, str(cfg[k])[:120])
                    mudou = True
            if mudou:
                c.save()
            if simular:
                transaction.set_rollback(True)

        self.stdout.write(
            f"Contas bancárias: {n['contas_novas']} nova(s), {n['contas_atualizadas']} atualizada(s) "
            f"(de {len(bancos)} no arquivo). Categorias: {n['cats_novas']} nova(s), {n['cats_atualizadas']} "
            f"atualizada(s) (de {len(cats)}). Regra do Agging: {'atualizada' if mudou else 'sem mudança'}."
            + (' [SIMULAÇÃO: nada foi gravado]' if simular else ''))
