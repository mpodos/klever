# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-10-03 16:25
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0006_auto_20160925_1829'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='reportroot',
            name='tasks_solved',
        ),
    ]