"""Índices do histórico, sem travar o portal.

Todo login grava no SystemLog, e o banco é compartilhado: um CREATE INDEX comum
seguraria essas escritas até terminar. Com CONCURRENTLY elas seguem, e por isso a
migração não pode rodar dentro de transação (`atomic = False`). Só funciona em
Postgres, que é o banco do portal; o sqlite dos testes nasce sem migrações.

Se ela for interrompida no meio, o Postgres deixa um índice inválido: apague-o com
DROP INDEX e rode de novo.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('core', '0026_limpeza_historico'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='systemlog',
            index=models.Index(fields=['-created_at', '-id'], name='core_syslog_criado_idx'),
        ),
        AddIndexConcurrently(
            model_name='systemlog',
            index=models.Index(fields=['user', '-created_at'], name='core_syslog_user_criado_idx'),
        ),
        AddIndexConcurrently(
            model_name='systemlog',
            index=models.Index(fields=['action_type', '-created_at'], name='core_syslog_acao_criado_idx'),
        ),
    ]
