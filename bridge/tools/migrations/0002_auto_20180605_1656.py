# Generated by Django 2.0.4 on 2018-06-05 13:56

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tools', '0001_initial')]

    operations = [
        migrations.AlterField(model_name='calllogs', name='execution_delta', field=models.FloatField(default=0)),
        migrations.AlterField(model_name='calllogs', name='is_failed', field=models.BooleanField(default=True)),
    ]