from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Sum, Q, Prefetch
from django.utils import timezone
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from .models import (
    KnowledgeTrail, TrailModule, Lesson, QuizQuestion, QuizOption, QuizAnswer,
    TrailProgress, LessonProgress, Certificate
)
from users.models import Sector
import json


@login_required
def trails_dashboard(request):
    """Dashboard principal de trilhas de conhecimento"""
    user = request.user
    
    # Buscar todas as trilhas ativas
    trails = KnowledgeTrail.objects.filter(is_active=True).select_related('sector').prefetch_related(
        'modules__lessons'
    ).annotate(
        total_modules=Count('modules', filter=Q(modules__is_active=True)),
        total_lessons=Count('modules__lessons', filter=Q(
            modules__is_active=True,
            modules__lessons__is_active=True
        ))
    )
    
    # Determinar setores que o usuário pode gerenciar
    user_sectors = []
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            # SUPERADMIN e ADMIN veem todas as trilhas
            user_sectors = None  # None significa todos
        elif user.hierarchy == 'SUPERVISOR':
            # SUPERVISOR vê apenas trilhas do seu setor
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
    
    # Adicionar progresso do usuário
    trail_data = []
    for trail in trails:
        progress = trail.get_progress(user)
        completion = trail.get_completion_percentage(user)
        
        # Determinar se pode gerenciar esta trilha
        can_manage = False
        if user_sectors is None:  # SUPERADMIN
            can_manage = True
        elif user_sectors and trail.sector in user_sectors:  # SUPERVISOR do setor
            can_manage = True
        
        trail_data.append({
            'trail': trail,
            'progress': progress,
            'completion': completion,
            'can_manage': can_manage
        })
    
    # Estatísticas do usuário
    user_stats = {
        'total_trails_started': TrailProgress.objects.filter(
            user=user,
            status__in=['in_progress', 'completed']
        ).count(),
        'total_trails_completed': TrailProgress.objects.filter(
            user=user,
            status='completed'
        ).count(),
        'total_points': TrailProgress.objects.filter(user=user).aggregate(
            total=Sum('total_points_earned')
        )['total'] or 0,
        'total_lessons_completed': LessonProgress.objects.filter(
            user=user,
            completed=True
        ).count(),
    }
    
    # Certificados do usuário
    certificates = Certificate.objects.filter(user=user).select_related('trail').order_by('-issued_at')[:5]
    
    # Verificar se pode criar trilhas
    can_create = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN', 'SUPERVISOR']:
            can_create = True
    
    context = {
        'trail_data': trail_data,
        'user_stats': user_stats,
        'certificates': certificates,
        'can_create': can_create,
    }
    
    return render(request, 'knowledge_trails/dashboard.html', context)


@login_required
def trail_detail(request, trail_id):
    """Visualização detalhada de uma trilha com minimapa"""
    trail = get_object_or_404(
        KnowledgeTrail.objects.prefetch_related(
            Prefetch('modules', queryset=TrailModule.objects.filter(is_active=True).prefetch_related(
                Prefetch('lessons', queryset=Lesson.objects.filter(is_active=True))
            ))
        ),
        id=trail_id,
        is_active=True
    )
    
    user = request.user
    progress = trail.get_progress(user)
    completion = trail.get_completion_percentage(user)
    
    # Verificar permissão de gerenciamento
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            # SUPERADMIN e ADMIN podem gerenciar todas as trilhas
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            # SUPERVISOR pode gerenciar trilhas do seu setor
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    # Preparar dados dos módulos
    modules_data = []
    for module in trail.modules.all():
        is_unlocked = module.is_unlocked_for_user(user)
        lessons_data = []
        
        for lesson in module.lessons.all():
            lesson_progress = LessonProgress.objects.filter(
                lesson=lesson,
                user=user
            ).first()
            
            lessons_data.append({
                'lesson': lesson,
                'progress': lesson_progress,
                'is_unlocked': lesson.is_unlocked_for_user(user) if is_unlocked else False,
                'completed': lesson_progress.completed if lesson_progress else False
            })
        
        completed_lessons = sum(1 for l in lessons_data if l['completed'])
        total_lessons = len(lessons_data)
        
        modules_data.append({
            'module': module,
            'is_unlocked': is_unlocked,
            'lessons': lessons_data,
            'completed_lessons': completed_lessons,
            'total_lessons': total_lessons,
            'completion_percentage': round((completed_lessons / total_lessons * 100)) if total_lessons > 0 else 0
        })
    
    context = {
        'trail': trail,
        'progress': progress,
        'completion': completion,
        'modules_data': modules_data,
        'can_manage': can_manage,
    }
    
    return render(request, 'knowledge_trails/trail_detail.html', context)


@login_required
def lesson_view(request, lesson_id):
    """Visualização de uma lição específica"""
    lesson = get_object_or_404(
        Lesson.objects.select_related('module__trail').prefetch_related('quiz_questions__options'),
        id=lesson_id,
        is_active=True
    )
    
    user = request.user
    trail = lesson.module.trail
    
    # Verificar se a lição está desbloqueada
    if not lesson.is_unlocked_for_user(user):
        messages.error(request, 'Esta lição ainda está bloqueada. Complete as lições anteriores primeiro.')
        return redirect('knowledge_trails:trail_detail', trail_id=trail.id)
    
    # Buscar ou criar progresso
    lesson_progress, created = LessonProgress.objects.get_or_create(
        lesson=lesson,
        user=user
    )
    
    # Se for POST, processar conclusão ou quiz
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'complete' and lesson.lesson_type != 'quiz':
            # Marcar como concluída
            lesson_progress.mark_completed()
            messages.success(request, f'✅ Lição "{lesson.title}" concluída! Você ganhou {lesson.points} pontos!')
            
            # Verificar se a trilha foi concluída
            trail_progress = trail.get_progress(user)
            if trail_progress.status == 'completed' and trail.enable_certificate:
                # Gerar certificado
                certificate, cert_created = Certificate.objects.get_or_create(
                    trail_progress=trail_progress,
                    user=user,
                    trail=trail
                )
                if cert_created:
                    messages.success(request, '🎉 Parabéns! Você concluiu a trilha e recebeu um certificado!')
                    return redirect('knowledge_trails:certificate_view', certificate_id=certificate.id)
            
            # Ir para próxima lição ou voltar à trilha
            next_lesson = Lesson.objects.filter(
                module=lesson.module,
                order__gt=lesson.order,
                is_active=True
            ).first()
            
            if next_lesson:
                return redirect('knowledge_trails:lesson_view', lesson_id=next_lesson.id)
            else:
                # Próximo módulo
                next_module = TrailModule.objects.filter(
                    trail=trail,
                    order__gt=lesson.module.order,
                    is_active=True
                ).first()
                
                if next_module:
                    first_lesson = next_module.lessons.filter(is_active=True).first()
                    if first_lesson and first_lesson.is_unlocked_for_user(user):
                        return redirect('knowledge_trails:lesson_view', lesson_id=first_lesson.id)
                
                return redirect('knowledge_trails:trail_detail', trail_id=trail.id)
        
        elif action == 'submit_quiz' and lesson.lesson_type == 'quiz':
            # Processar quiz
            quiz_questions = lesson.quiz_questions.all()
            correct_answers = 0
            total_questions = quiz_questions.count()
            
            # Incrementar tentativa antes de salvar respostas
            lesson_progress.quiz_attempts += 1
            attempt_number = lesson_progress.quiz_attempts
            
            for question in quiz_questions:
                selected_option_id = request.POST.get(f'question_{question.id}')
                if selected_option_id:
                    selected_option = QuizOption.objects.filter(id=selected_option_id).first()
                    if selected_option:
                        is_correct = selected_option.is_correct
                        if is_correct:
                            correct_answers += 1
                        
                        # Salvar resposta do usuário
                        QuizAnswer.objects.create(
                            user=user,
                            question=question,
                            selected_option=selected_option,
                            is_correct=is_correct,
                            attempt_number=attempt_number
                        )
            
            score = round((correct_answers / total_questions * 100)) if total_questions > 0 else 0
            lesson_progress.quiz_score = score
            
            # Considerar aprovado com 70% ou mais
            if score >= 70:
                lesson_progress.mark_completed()
                messages.success(request, f'✅ Quiz concluído! Pontuação: {score}%. Você ganhou {lesson.points} pontos!')
                
                # Verificar conclusão da trilha
                trail_progress = trail.get_progress(user)
                if trail_progress.status == 'completed' and trail.enable_certificate:
                    certificate, cert_created = Certificate.objects.get_or_create(
                        trail_progress=trail_progress,
                        user=user,
                        trail=trail
                    )
                    if cert_created:
                        messages.success(request, '🎉 Parabéns! Você concluiu a trilha e recebeu um certificado!')
                        return redirect('knowledge_trails:certificate_view', certificate_id=certificate.id)
                
                return redirect('knowledge_trails:trail_detail', trail_id=trail.id)
            else:
                lesson_progress.save()
                messages.warning(request, f'⚠️ Pontuação: {score}%. Você precisa de pelo menos 70% para passar. Tente novamente!')
    
    # Buscar próxima lição
    next_lesson = Lesson.objects.filter(
        module=lesson.module,
        order__gt=lesson.order,
        is_active=True
    ).first()
    
    context = {
        'lesson': lesson,
        'trail': trail,
        'lesson_progress': lesson_progress,
        'next_lesson': next_lesson,
    }
    
    return render(request, 'knowledge_trails/lesson_view.html', context)


@login_required
def leaderboard(request, trail_id):
    """Ranking de usuários em uma trilha"""
    trail = get_object_or_404(KnowledgeTrail, id=trail_id, is_active=True)
    
    # Buscar progresso de usuários
    leaderboard_data = TrailProgress.objects.filter(
        trail=trail
    ).select_related('user').order_by('-total_points_earned', 'completed_at')[:50]
    
    # Posição do usuário atual
    user_progress = trail.get_progress(request.user)
    user_rank = TrailProgress.objects.filter(
        trail=trail,
        total_points_earned__gt=user_progress.total_points_earned
    ).count() + 1
    
    context = {
        'trail': trail,
        'leaderboard': leaderboard_data,
        'user_progress': user_progress,
        'user_rank': user_rank,
    }
    
    return render(request, 'knowledge_trails/leaderboard.html', context)


@login_required
def certificate_view(request, certificate_id):
    """Visualização de certificado"""
    certificate = get_object_or_404(
        Certificate.objects.select_related('user', 'trail'),
        id=certificate_id
    )
    
    # Verificar se o usuário pode ver este certificado
    if certificate.user != request.user and not request.user.is_superuser:
        messages.error(request, 'Você não tem permissão para ver este certificado.')
        return redirect('knowledge_trails:dashboard')
    
    context = {
        'certificate': certificate,
    }
    
    return render(request, 'knowledge_trails/certificate.html', context)


@login_required
def download_certificate_pdf(request, certificate_id):
    """Download do certificado em PDF"""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from io import BytesIO
    
    certificate = get_object_or_404(
        Certificate.objects.select_related('user', 'trail'),
        id=certificate_id
    )
    
    # Verificar permissão
    if certificate.user != request.user and not request.user.is_superuser:
        messages.error(request, 'Você não tem permissão para baixar este certificado.')
        return redirect('knowledge_trails:dashboard')
    
    # Criar PDF
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)
    
    # Fundo
    p.setFillColorRGB(0.95, 0.95, 0.98)
    p.rect(0, 0, width, height, fill=1)
    
    # Borda decorativa
    p.setStrokeColorRGB(0.2, 0.3, 0.6)
    p.setLineWidth(3)
    p.rect(1*cm, 1*cm, width-2*cm, height-2*cm)
    
    p.setLineWidth(1)
    p.rect(1.5*cm, 1.5*cm, width-3*cm, height-3*cm)
    
    # Logo (se existir)
    if certificate.trail.certificate_logo:
        try:
            logo = ImageReader(certificate.trail.certificate_logo.path)
            p.drawImage(logo, width/2 - 3*cm, height - 6*cm, width=6*cm, height=3*cm, mask='auto')
        except:
            pass
    
    # Título
    p.setFillColorRGB(0.2, 0.3, 0.6)
    p.setFont("Helvetica-Bold", 36)
    p.drawCentredString(width/2, height - 8*cm, "CERTIFICADO")
    
    p.setFont("Helvetica", 16)
    p.drawCentredString(width/2, height - 9.5*cm, "DE CONCLUSÃO")
    
    # Texto principal
    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica", 14)
    p.drawCentredString(width/2, height - 11.5*cm, "Certificamos que")
    
    p.setFont("Helvetica-Bold", 24)
    p.setFillColorRGB(0.2, 0.3, 0.6)
    p.drawCentredString(width/2, height - 13.5*cm, certificate.user.get_full_name())
    
    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica", 14)
    p.drawCentredString(width/2, height - 15*cm, "concluiu com êxito a trilha de conhecimento")
    
    p.setFont("Helvetica-Bold", 18)
    p.setFillColorRGB(0.2, 0.3, 0.6)
    p.drawCentredString(width/2, height - 16.5*cm, certificate.trail.title)
    
    # Informações adicionais
    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica", 12)
    p.drawCentredString(width/2, height - 18*cm, 
                       f"Carga horária: {certificate.trail.estimated_hours}h | "
                       f"Pontuação: {certificate.trail_progress.total_points_earned} pontos")
    
    # Data e código
    p.setFont("Helvetica", 10)
    p.drawCentredString(width/2, 3*cm, 
                       f"Emitido em: {certificate.issued_at.strftime('%d de %B de %Y')}")
    p.drawCentredString(width/2, 2.5*cm, 
                       f"Código de Verificação: {certificate.certificate_code}")
    
    p.showPage()
    p.save()
    
    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="certificado_{certificate.certificate_code}.pdf"'
    
    return response


# ============= VIEWS DE GERENCIAMENTO (SUPERVISORES) =============

@login_required
def manage_trail(request, trail_id):
    """Área de gerenciamento de trilha para supervisores"""
    trail = get_object_or_404(KnowledgeTrail, id=trail_id)
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            # SUPERADMIN e ADMIN podem gerenciar todas as trilhas
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            # SUPERVISOR pode gerenciar trilhas do seu setor
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para gerenciar esta trilha.')
        return redirect('knowledge_trails:dashboard')
    
    # Estatísticas
    total_users = TrailProgress.objects.filter(trail=trail).count()
    completed_users = TrailProgress.objects.filter(trail=trail, status='completed').count()
    in_progress_users = TrailProgress.objects.filter(trail=trail, status='in_progress').count()
    
    # Usuários que concluíram
    completed_list = TrailProgress.objects.filter(
        trail=trail,
        status='completed'
    ).select_related('user').order_by('-completed_at')[:20]
    
    # Ranking
    leaderboard = TrailProgress.objects.filter(
        trail=trail
    ).select_related('user').order_by('-total_points_earned')[:10]
    
    context = {
        'trail': trail,
        'total_users': total_users,
        'completed_users': completed_users,
        'in_progress_users': in_progress_users,
        'completion_rate': round((completed_users / total_users * 100)) if total_users > 0 else 0,
        'completed_list': completed_list,
        'leaderboard': leaderboard,
    }
    
    return render(request, 'knowledge_trails/manage_trail.html', context)


@login_required
def edit_trail_map(request, trail_id):
    """Editor do minimapa da trilha"""
    trail = get_object_or_404(KnowledgeTrail, id=trail_id)
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            # SUPERADMIN e ADMIN podem gerenciar todas as trilhas
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            # SUPERVISOR pode gerenciar trilhas do seu setor
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para editar esta trilha.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        # Atualizar posições dos módulos
        modules_data = json.loads(request.POST.get('modules_data', '[]'))
        
        for module_data in modules_data:
            module = TrailModule.objects.filter(id=module_data['id'], trail=trail).first()
            if module:
                module.map_x = module_data['x']
                module.map_y = module_data['y']
                module.save()
        
        messages.success(request, '✅ Minimapa atualizado com sucesso!')
        return redirect('knowledge_trails:manage_trail', trail_id=trail.id)
    
    modules = trail.modules.filter(is_active=True).prefetch_related('lessons')
    
    context = {
        'trail': trail,
        'modules': modules,
    }
    
    return render(request, 'knowledge_trails/edit_trail_map.html', context)


# ============= VIEWS DE CRUD DE TRILHAS =============

@login_required
def create_trail(request):
    """Criar nova trilha"""
    user = request.user
    
    # Verificar permissão
    can_create = False
    user_sectors = []
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_create = True
            user_sectors = Sector.objects.all()
        elif user.hierarchy == 'SUPERVISOR':
            can_create = True
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
    
    if not can_create:
        messages.error(request, 'Você não tem permissão para criar trilhas.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        sector_id = request.POST.get('sector')
        icon = request.POST.get('icon', '📚')
        color = request.POST.get('color', '#6366f1')
        difficulty = request.POST.get('difficulty', 'beginner')
        estimated_hours = request.POST.get('estimated_hours', 1)
        enable_certificate = request.POST.get('enable_certificate') == 'on'
        
        # Validar campos obrigatórios
        if not title or not sector_id:
            messages.error(request, 'Título e Setor são obrigatórios.')
        else:
            sector = get_object_or_404(Sector, id=sector_id)
            
            # Verificar se supervisor pode criar trilha neste setor
            if user.hierarchy == 'SUPERVISOR':
                if sector not in user_sectors:
                    messages.error(request, 'Você não tem permissão para criar trilhas neste setor.')
                    return redirect('knowledge_trails:create_trail')
            
            trail = KnowledgeTrail.objects.create(
                title=title,
                description=description,
                sector=sector,
                icon=icon,
                color=color,
                difficulty=difficulty,
                estimated_hours=estimated_hours,
                enable_certificate=enable_certificate,
                created_by=user
            )
            
            messages.success(request, f'✅ Trilha "{trail.title}" criada com sucesso!')
            return redirect('knowledge_trails:edit_trail', trail_id=trail.id)
    
    context = {
        'sectors': user_sectors,
        'difficulty_choices': KnowledgeTrail.DIFFICULTY_CHOICES,
    }
    
    return render(request, 'knowledge_trails/create_trail.html', context)


@login_required
def edit_trail(request, trail_id):
    """Editar trilha existente"""
    trail = get_object_or_404(KnowledgeTrail, id=trail_id)
    user = request.user
    
    # Verificar permissão
    can_manage = False
    user_sectors = []
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
            user_sectors = Sector.objects.all()
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para editar esta trilha.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        trail.title = request.POST.get('title', trail.title)
        trail.description = request.POST.get('description', trail.description)
        sector_id = request.POST.get('sector')
        
        if sector_id:
            sector = get_object_or_404(Sector, id=sector_id)
            # Verificar permissão para este setor
            if user.hierarchy == 'SUPERVISOR' and sector not in user_sectors:
                messages.error(request, 'Você não tem permissão para mover trilhas para este setor.')
            else:
                trail.sector = sector
        
        trail.icon = request.POST.get('icon', trail.icon)
        trail.color = request.POST.get('color', trail.color)
        trail.difficulty = request.POST.get('difficulty', trail.difficulty)
        trail.estimated_hours = request.POST.get('estimated_hours', trail.estimated_hours)
        trail.enable_certificate = request.POST.get('enable_certificate') == 'on'
        trail.is_active = request.POST.get('is_active') == 'on'
        
        trail.save()
        
        messages.success(request, f'✅ Trilha "{trail.title}" atualizada com sucesso!')
        return redirect('knowledge_trails:manage_trail', trail_id=trail.id)
    
    context = {
        'trail': trail,
        'sectors': user_sectors,
        'difficulty_choices': KnowledgeTrail.DIFFICULTY_CHOICES,
        'modules': trail.modules.filter(is_active=True).prefetch_related('lessons'),
    }
    
    return render(request, 'knowledge_trails/edit_trail.html', context)


@login_required
@require_POST
def delete_trail(request, trail_id):
    """Excluir trilha"""
    trail = get_object_or_404(KnowledgeTrail, id=trail_id)
    user = request.user
    
    # Verificar permissão
    can_delete = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_delete = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_delete = trail.sector in user_sectors
    
    if not can_delete:
        messages.error(request, 'Você não tem permissão para excluir esta trilha.')
        return redirect('knowledge_trails:dashboard')
    
    # Verificar se há usuários com progresso
    has_progress = TrailProgress.objects.filter(trail=trail).exists()
    
    if has_progress:
        # Apenas desativar se houver progresso
        trail.is_active = False
        trail.save()
        messages.warning(request, f'⚠️ Trilha "{trail.title}" foi desativada pois há usuários com progresso.')
    else:
        # Pode excluir permanentemente
        trail_title = trail.title
        trail.delete()
        messages.success(request, f'✅ Trilha "{trail_title}" foi excluída permanentemente.')
    
    return redirect('knowledge_trails:dashboard')


@login_required
def create_module(request, trail_id):
    """Criar novo módulo em uma trilha"""
    trail = get_object_or_404(KnowledgeTrail, id=trail_id)
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para adicionar módulos nesta trilha.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description', '')
        icon_emoji = request.POST.get('icon_emoji', '📖')
        order = request.POST.get('order', 0)
        
        if not title:
            messages.error(request, 'Título é obrigatório.')
        else:
            # Posição padrão no minimapa (centro)
            module = TrailModule.objects.create(
                trail=trail,
                title=title,
                description=description,
                icon_emoji=icon_emoji,
                order=order,
                map_x=50,
                map_y=50
            )
            
            messages.success(request, f'✅ Módulo "{module.title}" criado com sucesso!')
            return redirect('knowledge_trails:edit_trail', trail_id=trail.id)
    
    context = {
        'trail': trail,
    }
    
    return render(request, 'knowledge_trails/create_module.html', context)


@login_required
@require_POST
def delete_module(request, module_id):
    """Excluir módulo"""
    module = get_object_or_404(TrailModule, id=module_id)
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para excluir este módulo.')
        return redirect('knowledge_trails:dashboard')
    
    module_title = module.title
    module.delete()
    messages.success(request, f'✅ Módulo "{module_title}" excluído com sucesso!')
    
    return redirect('knowledge_trails:edit_trail', trail_id=trail.id)


# ============= VIEWS DE CRUD DE LIÇÕES =============

@login_required
def create_lesson(request, module_id):
    """Criar nova lição em um módulo"""
    from .forms import LessonForm
    
    module = get_object_or_404(TrailModule.objects.select_related('trail'), id=module_id)
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para adicionar lições neste módulo.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        form = LessonForm(request.POST, request.FILES)
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.module = module
            lesson.save()
            
            messages.success(request, f'✅ Lição "{lesson.title}" criada com sucesso!')
            
            # Se for quiz, redirecionar para adicionar questões
            if lesson.lesson_type == 'quiz':
                return redirect('knowledge_trails:edit_lesson_quiz', lesson_id=lesson.id)
            
            return redirect('knowledge_trails:edit_trail', trail_id=trail.id)
    else:
        form = LessonForm()
    
    context = {
        'form': form,
        'module': module,
        'trail': trail,
    }
    
    return render(request, 'knowledge_trails/create_lesson.html', context)


@login_required
def edit_lesson(request, lesson_id):
    """Editar lição existente"""
    from .forms import LessonForm
    
    lesson = get_object_or_404(Lesson.objects.select_related('module__trail'), id=lesson_id)
    module = lesson.module
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para editar esta lição.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        form = LessonForm(request.POST, request.FILES, instance=lesson)
        if form.is_valid():
            lesson = form.save()
            messages.success(request, f'✅ Lição "{lesson.title}" atualizada com sucesso!')
            return redirect('knowledge_trails:edit_trail', trail_id=trail.id)
    else:
        form = LessonForm(instance=lesson)
    
    context = {
        'form': form,
        'lesson': lesson,
        'module': module,
        'trail': trail,
    }
    
    return render(request, 'knowledge_trails/edit_lesson.html', context)


@login_required
def edit_lesson_quiz(request, lesson_id):
    """Editar questões do quiz de uma lição"""
    from .forms import QuizQuestionForm, QuizOptionForm
    
    lesson = get_object_or_404(
        Lesson.objects.select_related('module__trail').prefetch_related('quiz_questions__options'),
        id=lesson_id
    )
    module = lesson.module
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para editar este quiz.')
        return redirect('knowledge_trails:dashboard')
    
    if lesson.lesson_type != 'quiz':
        messages.error(request, 'Esta lição não é um quiz.')
        return redirect('knowledge_trails:edit_lesson', lesson_id=lesson.id)
    
    # Buscar questões existentes
    questions = lesson.quiz_questions.all().prefetch_related('options')
    
    context = {
        'lesson': lesson,
        'module': module,
        'trail': trail,
        'questions': questions,
    }
    
    return render(request, 'knowledge_trails/edit_lesson_quiz.html', context)


@login_required
def create_quiz_question(request, lesson_id):
    """Criar nova questão de quiz"""
    from .forms import QuizQuestionForm
    
    lesson = get_object_or_404(Lesson.objects.select_related('module__trail'), id=lesson_id)
    module = lesson.module
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para adicionar questões neste quiz.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        form = QuizQuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)
            question.lesson = lesson
            question.save()
            
            messages.success(request, '✅ Questão criada com sucesso!')
            return redirect('knowledge_trails:edit_quiz_question', question_id=question.id)
    else:
        form = QuizQuestionForm()
    
    context = {
        'form': form,
        'lesson': lesson,
        'module': module,
        'trail': trail,
    }
    
    return render(request, 'knowledge_trails/create_quiz_question.html', context)


@login_required
def edit_quiz_question(request, question_id):
    """Editar questão de quiz e suas alternativas"""
    from .forms import QuizQuestionForm, QuizOptionForm
    
    question = get_object_or_404(
        QuizQuestion.objects.select_related('lesson__module__trail').prefetch_related('options'),
        id=question_id
    )
    lesson = question.lesson
    module = lesson.module
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para editar esta questão.')
        return redirect('knowledge_trails:dashboard')
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_question':
            form = QuizQuestionForm(request.POST, instance=question)
            if form.is_valid():
                form.save()
                messages.success(request, '✅ Questão atualizada com sucesso!')
        
        elif action == 'add_option':
            option_text = request.POST.get('option_text')
            is_correct = request.POST.get('is_correct') == 'on'
            
            if option_text:
                QuizOption.objects.create(
                    question=question,
                    option_text=option_text,
                    is_correct=is_correct
                )
                messages.success(request, '✅ Alternativa adicionada com sucesso!')
        
        elif action == 'delete_option':
            option_id = request.POST.get('option_id')
            option = QuizOption.objects.filter(id=option_id, question=question).first()
            if option:
                option.delete()
                messages.success(request, '✅ Alternativa excluída!')
        
        return redirect('knowledge_trails:edit_quiz_question', question_id=question.id)
    
    form = QuizQuestionForm(instance=question)
    options = question.options.all()
    
    # Verificar se existe pelo menos uma alternativa correta
    has_no_correct = options.exists() and not options.filter(is_correct=True).exists()
    
    context = {
        'form': form,
        'question': question,
        'options': options,
        'lesson': lesson,
        'module': module,
        'trail': trail,
        'has_no_correct': has_no_correct,
    }
    
    return render(request, 'knowledge_trails/edit_quiz_question.html', context)


@login_required
@require_POST
def delete_lesson(request, lesson_id):
    """Excluir lição"""
    lesson = get_object_or_404(Lesson.objects.select_related('module__trail'), id=lesson_id)
    module = lesson.module
    trail = module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para excluir esta lição.')
        return redirect('knowledge_trails:dashboard')
    
    lesson_title = lesson.title
    lesson.delete()
    messages.success(request, f'✅ Lição "{lesson_title}" excluída com sucesso!')
    
    return redirect('knowledge_trails:edit_trail', trail_id=trail.id)


@login_required
@require_POST
def delete_quiz_question(request, question_id):
    """Excluir questão de quiz"""
    question = get_object_or_404(QuizQuestion.objects.select_related('lesson__module__trail'), id=question_id)
    lesson = question.lesson
    trail = lesson.module.trail
    user = request.user
    
    # Verificar permissão
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para excluir esta questão.')
        return redirect('knowledge_trails:dashboard')
    
    question.delete()
    messages.success(request, '✅ Questão excluída com sucesso!')
    
    return redirect('knowledge_trails:edit_lesson_quiz', lesson_id=lesson.id)


@login_required
def quiz_answers_view(request, lesson_id):
    """Visualizar respostas de um quiz por todos os usuários"""
    lesson = get_object_or_404(
        Lesson.objects.select_related('module__trail'),
        id=lesson_id,
        lesson_type='quiz'
    )
    trail = lesson.module.trail
    user = request.user
    
    # Verificar permissão de gerenciamento
    can_manage = False
    if hasattr(user, 'hierarchy'):
        if user.hierarchy in ['SUPERADMIN', 'ADMIN'] or user.is_superuser:
            can_manage = True
        elif user.hierarchy == 'SUPERVISOR':
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            can_manage = trail.sector in user_sectors
    
    if not can_manage:
        messages.error(request, 'Você não tem permissão para ver as respostas deste quiz.')
        return redirect('knowledge_trails:dashboard')
    
    # Buscar todos os usuários que responderam o quiz
    user_progresses = LessonProgress.objects.filter(
        lesson=lesson,
        quiz_attempts__gt=0
    ).select_related('user').order_by('-quiz_score', 'completed_at')
    
    # Buscar questões do quiz
    questions = lesson.quiz_questions.all().prefetch_related('options')
    
    # Preparar dados dos usuários com suas respostas
    users_data = []
    for progress in user_progresses:
        user_answers = QuizAnswer.objects.filter(
            user=progress.user,
            question__lesson=lesson,
            attempt_number=progress.quiz_attempts  # Última tentativa
        ).select_related('question', 'selected_option')
        
        answers_dict = {answer.question_id: answer for answer in user_answers}
        
        users_data.append({
            'user': progress.user,
            'progress': progress,
            'answers': answers_dict
        })
    
    context = {
        'lesson': lesson,
        'trail': trail,
        'questions': questions,
        'users_data': users_data,
        'total_users': len(users_data),
    }
    
    return render(request, 'knowledge_trails/quiz_answers.html', context)


@login_required
def user_quiz_detail(request, lesson_id, user_id):
    """Ver detalhes das respostas de um usuário específico em um quiz"""
    lesson = get_object_or_404(
        Lesson.objects.select_related('module__trail'),
        id=lesson_id,
        lesson_type='quiz'
    )
    trail = lesson.module.trail
    current_user = request.user
    target_user = get_object_or_404(User, id=user_id)
    
    # Verificar permissão
    can_view = False
    if current_user == target_user:
        can_view = True
    elif hasattr(current_user, 'hierarchy'):
        if current_user.hierarchy in ['SUPERADMIN', 'ADMIN'] or current_user.is_superuser:
            can_view = True
        elif current_user.hierarchy == 'SUPERVISOR':
            user_sectors = list(current_user.sectors.all())
            if current_user.sector:
                user_sectors.append(current_user.sector)
            can_view = trail.sector in user_sectors
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para ver estas respostas.')
        return redirect('knowledge_trails:dashboard')
    
    # Progresso do usuário
    lesson_progress = LessonProgress.objects.filter(
        lesson=lesson,
        user=target_user
    ).first()
    
    if not lesson_progress:
        messages.error(request, 'Este usuário ainda não respondeu o quiz.')
        return redirect('knowledge_trails:quiz_answers', lesson_id=lesson.id)
    
    # Buscar todas as tentativas
    attempts = QuizAnswer.objects.filter(
        user=target_user,
        question__lesson=lesson
    ).values('attempt_number').distinct().order_by('-attempt_number')
    
    # Tentativa selecionada (última por padrão)
    selected_attempt = request.GET.get('attempt', lesson_progress.quiz_attempts)
    try:
        selected_attempt = int(selected_attempt)
    except:
        selected_attempt = lesson_progress.quiz_attempts
    
    # Buscar questões com respostas do usuário para a tentativa selecionada
    questions = lesson.quiz_questions.all().prefetch_related('options')
    user_answers = QuizAnswer.objects.filter(
        user=target_user,
        question__lesson=lesson,
        attempt_number=selected_attempt
    ).select_related('selected_option')
    
    answers_dict = {answer.question_id: answer for answer in user_answers}
    
    # Calcular estatísticas da tentativa
    correct_count = user_answers.filter(is_correct=True).count()
    total_questions = questions.count()
    attempt_score = round((correct_count / total_questions * 100)) if total_questions > 0 else 0
    
    context = {
        'lesson': lesson,
        'trail': trail,
        'target_user': target_user,
        'lesson_progress': lesson_progress,
        'questions': questions,
        'answers_dict': answers_dict,
        'attempts': attempts,
        'selected_attempt': selected_attempt,
        'correct_count': correct_count,
        'total_questions': total_questions,
        'attempt_score': attempt_score,
    }
    
    return render(request, 'knowledge_trails/user_quiz_detail.html', context)
