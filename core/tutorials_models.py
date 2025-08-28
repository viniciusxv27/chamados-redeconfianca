from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class Tutorial(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    pdf_file = models.FileField(upload_to='tutorials/')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'title']

    def __str__(self):
        return self.title
