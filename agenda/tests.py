from django.test import TestCase
from django.urls import reverse

from users.models import User

from .models import MeetingTranscription


class TranscriptionVisibilityTests(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            username='superadmin',
            email='superadmin@example.com',
            password='pass123',
            hierarchy='SUPERADMIN',
        )
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pass123',
            hierarchy='PADRAO',
        )
        self.other_owner = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='pass123',
            hierarchy='PADRAO',
        )
        self.viewer = User.objects.create_user(
            username='viewer',
            email='viewer@example.com',
            password='pass123',
            hierarchy='PADRAO',
        )

        self.own_transcription = MeetingTranscription.objects.create(
            owner=self.viewer,
            title='Transcrição própria',
            status='completed',
        )
        self.shared_transcription = MeetingTranscription.objects.create(
            owner=self.owner,
            title='Transcrição compartilhada',
            status='completed',
        )
        self.shared_transcription.shared_with.add(self.viewer)
        self.private_transcription = MeetingTranscription.objects.create(
            owner=self.other_owner,
            title='Transcrição privada de outro usuário',
            status='completed',
        )

    def test_superadmin_can_list_all_transcriptions(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse('agenda:transcription_list'))

        self.assertContains(response, self.own_transcription.title)
        self.assertContains(response, self.shared_transcription.title)
        self.assertContains(response, self.private_transcription.title)

    def test_regular_user_list_remains_limited_to_own_and_shared_transcriptions(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('agenda:transcription_list'))

        self.assertContains(response, self.own_transcription.title)
        self.assertContains(response, self.shared_transcription.title)
        self.assertNotContains(response, self.private_transcription.title)

    def test_superadmin_can_open_transcription_from_another_owner(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(
            reverse('agenda:transcription_detail', args=[self.private_transcription.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.private_transcription.title)

    def test_regular_user_cannot_open_unshared_transcription_from_another_owner(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse('agenda:transcription_detail', args=[self.private_transcription.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_superadmin_can_poll_status_for_transcription_from_another_owner(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(
            reverse('agenda:api_transcription_status', args=[self.private_transcription.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], self.private_transcription.pk)
