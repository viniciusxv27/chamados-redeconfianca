from rest_framework import serializers
from .models import Communication, CommunicationRead
from users.serializers import UserSerializer


class CommunicationSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    recipients = UserSerializer(many=True, read_only=True)
    
    class Meta:
        model = Communication
        fields = '__all__'
        read_only_fields = ['sender', 'created_at']


class CommunicationReadSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    communication = CommunicationSerializer(read_only=True)
    
    class Meta:
        model = CommunicationRead
        fields = '__all__'
