"""
Admin site bindings for contentstore
"""


from config_models.admin import ConfigurationModelAdmin
from django.contrib import admin

from cms.djangoapps.contentstore.models import VideoUploadConfig
from cms.djangoapps.contentstore.outlines_backfill import CourseOutlineBackfill, CourseOutlineBackfillAdmin

admin.site.register(VideoUploadConfig, ConfigurationModelAdmin)
admin.site.register(CourseOutlineBackfill, CourseOutlineBackfillAdmin)
