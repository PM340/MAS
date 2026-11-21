from django.db.models import Q
from .models import Message, MessageReadReceipt


def get_visible_messages_for_user(user):
    """Return queryset of direct messages and announcements visible to the given user."""
    if not user.is_authenticated:
        return Message.objects.none()

    if user.is_super_admin:
        return Message.objects.all()

    if user.is_teacher:
        assigned_ids = list(user.assigned_classes.values_list('id', flat=True))
        return Message.objects.filter(
            Q(recipient=user)
            | Q(sender=user)
            | Q(message_type=Message.TYPE_ANNOUNCEMENT, target_class__isnull=True)
            | Q(message_type=Message.TYPE_ANNOUNCEMENT, target_class_id__in=assigned_ids)
        ).distinct()

    # Student
    return Message.objects.filter(
        Q(recipient=user)
        | Q(message_type=Message.TYPE_ANNOUNCEMENT, target_class__isnull=True)
        | Q(message_type=Message.TYPE_ANNOUNCEMENT, target_class=user.student_class)
    ).distinct()


def get_unread_message_count(user):
    """Count unread visible messages (excluding messages sent by the user themselves)."""
    if not user.is_authenticated:
        return 0
    visible = get_visible_messages_for_user(user).exclude(sender=user)
    read_ids = MessageReadReceipt.objects.filter(user=user).values_list('message_id', flat=True)
    return visible.exclude(id__in=read_ids).count()


def lms_global_context(request):
    """Inject unread message notifications and role helpers into all templates."""
    if request.user.is_authenticated:
        return {
            'unread_message_count': get_unread_message_count(request.user),
        }
    return {
        'unread_message_count': 0,
    }
