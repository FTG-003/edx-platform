# Generated by Django 2.2.20 on 2021-05-27 17:07

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('course_overviews', '0024_overview_adds_has_highlights'),
        ('contentstore', '0005_add_enable_checklists_quality_waffle_flag'),
    ]

    operations = [
        migrations.CreateModel(
            name='CourseOutlineBackfill',
            fields=[
            ],
            options={
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('course_overviews.courseoverview',),
        ),
    ]
