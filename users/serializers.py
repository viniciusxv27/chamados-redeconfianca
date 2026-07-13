from rest_framework import serializers
from .models import User, Sector


class SectorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sector
        fields = ['id', 'name', 'description', 'created_at']


class UserSerializer(serializers.ModelSerializer):
    sector_name = serializers.CharField(source='sector.name', read_only=True)
    hierarchy_display = serializers.CharField(source='get_hierarchy_display', read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 
            'sector', 'sector_name', 'hierarchy', 'hierarchy_display',
            'balance_cs', 'phone', 'is_active', 'created_at'
        ]
        extra_kwargs = {
            'password': {'write_only': True}
        }
    
    def validate_hierarchy(self, value):
        """Impede que um usuário atribua, via API, hierarquia acima da sua."""
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and not user.can_assign_hierarchy(value):
            raise serializers.ValidationError('Você não pode atribuir uma hierarquia superior à sua.')
        return value

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = User.objects.create_user(**validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user
