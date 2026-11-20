from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .models import Skill
from .normalizer import invalidate_index


@receiver(post_save, sender=Skill)
@receiver(post_delete, sender=Skill)
def _skill_changed(**kwargs):
    invalidate_index()


@receiver(m2m_changed, sender=Skill.prerequisites.through)
@receiver(m2m_changed, sender=Skill.related.through)
def _skill_links_changed(**kwargs):
    invalidate_index()
