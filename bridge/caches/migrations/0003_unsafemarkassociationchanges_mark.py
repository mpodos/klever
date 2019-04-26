# Generated by Django 2.1.3 on 2019-03-21 12:14

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('marks', '0001_initial'),
        ('caches', '0002_unsafemarkassociationchanges_job'),
    ]

    operations = [
        migrations.AddField(
            model_name='unsafemarkassociationchanges',
            name='mark',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='marks.MarkUnsafe'),
        ),
    ]