"""
Code which updates and backfills course outline data.

Uses a proxy model to enable a Django admin interface to trigger asynch
tasks which backfill/recreate course outline data.
"""
import logging
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.learning_sequences.api import key_supports_outlines

from .tasks import update_outline_from_modulestore_task, update_multiple_outlines_from_modulestore_task


# If an admin user requests the course outline backfilling of this number of courses -or- higher,
# create a celery task which creates a celery tasks to backfill each course outline.
TASK_TO_LAUNCH_TASKS_THRESHOLD = 20

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


class CourseOutlineBackfill(CourseOverview):
    """
    Proxy model for CourseOverview.

    Does *not* create/update/delete CourseOverview objects - only reads the objects.
    Uses the course IDs of the CourseOverview objects to determine which course
    outlines to re-build/backfill.
    """
    class Meta:
        proxy = True

    def __str__(self):
        """Represent ourselves with the course key."""
        return str(self.id)

    @classmethod
    def get_course_outline_ids(cls):
        """
        Returns all the CourseOverview object ids.
        """
        return cls.objects.values_list('id', flat=True)


def backfill_course_outlines_subset(modeladmin, request, queryset):
    """
    Create a celery task to backfill a single course outline for each passed-in course key.

    If the number of passed-in course keys is above a threshold, then instead create a celery task which
    will then create a celery task to backfill a single course outline for each passed-in course key.
    """
    all_course_keys_qs = queryset.values_list('id', flat=True)
    if len(all_course_keys_qs) >= TASK_TO_LAUNCH_TASKS_THRESHOLD:
        # Create the celery task-creating task.
        backfills = len(all_course_keys_qs)
        update_multiple_outlines_from_modulestore_task.delay([str(course_key) for course_key in all_course_keys_qs])
        log.info("Queuing task to create outline creation tasks for all (%s) courses.", backfills)
    else:
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
    This selection of all CourseOvervies occurs in the overridden changelist_view method below.
    """
    backfill_course_outlines_subset(modeladmin, request, queryset)
backfill_course_outlines_all.short_description = "Backfill *all* course outlines"


class CourseOutlineBackfillAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """
    Backfills the course outline for each selected course key.
    """
    list_display = ['id']
    ordering = ['id']
    search_fields = ['id']

    actions = [backfill_course_outlines_subset, backfill_course_outlines_all]

    def changelist_view(self, request, extra_context=None):
        """
        Overrides the admin's changelist_view & selects *all* the CourseOverviews
        when the custom backfill_course_outlines_all action is selected.
        """
        if 'action' in request.POST and request.POST['action'] == 'backfill_course_outlines_all':
            # Irregardless of what CourseOverviews are already selected, select *all* of them.
            post = request.POST.copy()
            post.setlist(ACTION_CHECKBOX_NAME, self.model.get_course_outline_ids())
            request._set_post(post)  # pylint: disable=protected-access
        return super().changelist_view(request, extra_context)
