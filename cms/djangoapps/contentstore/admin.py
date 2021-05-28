"""
Admin site bindings for contentstore
"""

import logging

from config_models.admin import ConfigurationModelAdmin
from django.contrib import admin

from cms.djangoapps.contentstore.models import VideoUploadConfig
from cms.djangoapps.contentstore.outlines_backfill import CourseOutlineBackfill
from openedx.core.djangoapps.content.learning_sequences.api import key_supports_outlines

from .tasks import update_outline_from_modulestore_task, update_all_outlines_from_modulestore_task


log = logging.getLogger(__name__)


class ReadOnlyAdminMixin(object):
    """
    Disables all editing capabilities for the admin's model.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_display_links = None
        self.readonly_fields = [f.name for f in self.model._meta.get_fields()]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions["delete_selected"]
        return actions

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):  # pylint: disable=unused-argument
        return False

    def save_model(self, request, obj, form, change):
        pass

    def delete_model(self, request, obj):
        pass

    def save_related(self, request, form, formsets, change):
        pass


def backfill_course_outlines_subset(modeladmin, request, queryset):
    """
    Create a celery task to backfill a single course outline for each passed-in course key.

    If the number of passed-in course keys is above a threshold, then instead create a celery task which
    will then create a celery task to backfill a single course outline for each passed-in course key.
    """
    all_course_keys_qs = queryset.values_list('id', flat=True)

    # Create a separate celery task for each course outline requested.
    backfills = 0
    for course_key in all_course_keys_qs:
        if key_supports_outlines(course_key):
            log.info("Queuing outline creation for %s", course_key)
            update_outline_from_modulestore_task.delay(str(course_key))
            backfills += 1
        else:
            log.info("Outlines not supported for %s - skipping", course_key)
    plural_suffix = 's' if backfills > 1 else ''
    modeladmin.message_user(
        request,
        f"Successfully requested {backfills} course outline{plural_suffix} to be backfilled."
    )
backfill_course_outlines_subset.short_description = "Backfill selected course outlines"


def backfill_course_outlines_all(modeladmin, request, queryset):
    """
    Custom admin action which backfills *all* the course outlines - no matter which CourseOverviews are selected.
    """
    update_all_outlines_from_modulestore_task.delay()
backfill_course_outlines_all.short_description = "Backfill *all* course outlines"


class CourseOutlineBackfillAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    Backfills the course outline for each selected course key.
    """
    list_display = ['id']
    ordering = ['id']
    search_fields = ['id']

    actions = [backfill_course_outlines_subset, backfill_course_outlines_all]


admin.site.register(VideoUploadConfig, ConfigurationModelAdmin)
admin.site.register(CourseOutlineBackfill, CourseOutlineBackfillAdmin)
