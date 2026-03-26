from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from contestacao.models import Contestation, ContestationCartDraft, ContestationHistory, ExclusionRecord
from users.models import Sector, User


def normalize_sector_name(value):
    if not value:
        return ''
    text = str(value).strip().upper()
    for prefix in ['LOJA ', 'LOJA_', 'LOJA-']:
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip()


def match_sector_to_filial(sector_name, filial):
    if not sector_name or not filial:
        return False
    sector_norm = normalize_sector_name(sector_name)
    filial_norm = str(filial).strip().upper()
    return (
        filial_norm == sector_norm
        or filial_norm.endswith(sector_norm)
        or sector_norm in filial_norm
        or filial_norm in sector_norm
    )


def get_recovery_maps_for_user(user, exclusion_ids):
    history_map = {}
    history_rows = (
        ContestationHistory.objects
        .filter(action='created', user=user)
        .filter(contestation__exclusion_id__in=exclusion_ids)
        .exclude(notes='')
        .order_by('-created_at')
        .values('notes', 'contestation__exclusion_id')
    )
    for row in history_rows:
        exclusion_id = row.get('contestation__exclusion_id')
        if not exclusion_id:
            continue
        exclusion_id = int(exclusion_id)
        if exclusion_id not in history_map:
            history_map[exclusion_id] = (row.get('notes') or '').strip()

    contestation_map = {}
    contestation_rows = (
        Contestation.objects
        .filter(requester=user, exclusion_id__in=exclusion_ids)
        .order_by('-created_at')
        .values('exclusion_id', 'reason', 'attachment')
    )
    for row in contestation_rows:
        exclusion_id = int(row['exclusion_id'])
        if exclusion_id not in contestation_map:
            contestation_map[exclusion_id] = {
                'reason': (row.get('reason') or '').strip(),
                'attachment': row.get('attachment'),
            }

    return history_map, contestation_map


class Command(BaseCommand):
    help = 'Recupera/cria carrinho de contestacao no banco com base em motivos ja existentes.'

    def add_arguments(self, parser):
        parser.add_argument('--sector-id', type=int, required=True, help='ID do setor para recuperar (ex.: 25).')
        parser.add_argument('--user-id', type=int, help='Recupera para um usuario especifico.')
        parser.add_argument('--user-email', type=str, help='Recupera para um usuario especifico por email.')
        parser.add_argument('--limit', type=int, default=0, help='Limita quantidade de vendas processadas (0 = sem limite).')
        parser.add_argument('--dry-run', action='store_true', help='Somente simula, sem gravar no banco.')

    def handle(self, *args, **options):
        sector_id = options['sector_id']
        user_id = options.get('user_id')
        user_email = options.get('user_email')
        limit = options.get('limit') or 0
        dry_run = bool(options.get('dry_run'))

        try:
            sector = Sector.objects.get(pk=sector_id)
        except Sector.DoesNotExist:
            raise CommandError(f'Setor {sector_id} nao encontrado.')

        users_qs = User.objects.filter(is_active=True)
        if user_id:
            users_qs = users_qs.filter(pk=user_id)
        elif user_email:
            users_qs = users_qs.filter(email__iexact=user_email.strip())
        else:
            users_qs = users_qs.filter(Q(sector_id=sector_id) | Q(sectors__id=sector_id)).distinct()

        users = list(users_qs)
        if not users:
            raise CommandError('Nenhum usuario encontrado para recuperar carrinho.')

        exclusions = [
            e for e in ExclusionRecord.objects.all().order_by('-imported_at')
            if match_sector_to_filial(sector.name, e.filial)
        ]
        if limit > 0:
            exclusions = exclusions[:limit]

        if not exclusions:
            raise CommandError('Nenhuma venda encontrada para o setor informado.')

        self.stdout.write(self.style.WARNING(
            f'Setor: {sector.name} (ID {sector.id}) | Usuarios: {len(users)} | Vendas candidatas: {len(exclusions)}'
        ))

        created = 0
        updated = 0
        skipped = 0

        exclusion_ids = [e.pk for e in exclusions]

        for user in users:
            existing_drafts = {
                d.exclusion_id: d
                for d in ContestationCartDraft.objects.filter(user=user, exclusion_id__in=exclusion_ids)
            }
            history_map, contestation_map = get_recovery_maps_for_user(user, exclusion_ids)

            for exclusion in exclusions:
                reason = history_map.get(exclusion.pk, '')
                if not reason:
                    reason = (contestation_map.get(exclusion.pk, {}) or {}).get('reason', '')
                if not reason:
                    reason = (exclusion.observacao or '').strip()
                if not reason:
                    skipped += 1
                    continue

                attachment = (contestation_map.get(exclusion.pk, {}) or {}).get('attachment')
                draft = existing_drafts.get(exclusion.pk)
                was_created = draft is None
                if was_created:
                    draft = ContestationCartDraft(user=user, exclusion=exclusion, reason=reason)

                changed = False
                if not draft.reason and reason:
                    draft.reason = reason
                    changed = True
                if attachment and not draft.attachment:
                    draft.attachment = attachment
                    changed = True

                if dry_run:
                    if was_created:
                        created += 1
                    elif changed:
                        updated += 1
                    else:
                        skipped += 1
                    continue

                if was_created:
                    if attachment:
                        draft.attachment = attachment
                    draft.save()
                    created += 1
                    existing_drafts[exclusion.pk] = draft
                elif changed:
                    draft.save(update_fields=['reason', 'attachment', 'updated_at'])
                    updated += 1
                else:
                    skipped += 1

        mode = 'DRY-RUN' if dry_run else 'EXECUTADO'
        self.stdout.write(self.style.SUCCESS(
            f'[{mode}] Carrinhos recuperados. Criados: {created} | Atualizados: {updated} | Ignorados: {skipped}'
        ))
