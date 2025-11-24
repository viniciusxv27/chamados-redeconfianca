from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_trails', '0002_lesson_document_file_lesson_is_required_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='quizquestion',
            old_name='question',
            new_name='question_text',
        ),
        migrations.RenameField(
            model_name='quizoption',
            old_name='text',
            new_name='option_text',
        ),
        migrations.AddField(
            model_name='quizquestion',
            name='points',
            field=models.PositiveIntegerField(default=10, help_text='Pontos ganhos ao acertar esta questão'),
        ),
    ]
