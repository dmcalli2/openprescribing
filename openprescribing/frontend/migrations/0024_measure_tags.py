# -*- coding: utf-8 -*-
# Generated by Django 1.9.1 on 2017-07-21 08:53
from __future__ import unicode_literals

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('frontend', '0023_presentation_is_current'),
    ]

    operations = [
        migrations.AddField(
            model_name='measure',
            name='tags',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=30),
                blank=True,
                default=['core'],
                size=None),
            preserve_default=False,
        ),
    ]
