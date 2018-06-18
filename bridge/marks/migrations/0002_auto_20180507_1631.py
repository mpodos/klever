# Generated by Django 2.0.4 on 2018-05-07 13:31

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reports', '0004_alter_fields'), ('marks', '0001_initial')]

    operations = [
        migrations.CreateModel(
            name='MarkUnknownAttr',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mark', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='attrs',
                                           to='marks.MarkUnknownHistory')),
                ('is_compare', models.BooleanField(default=True)),
                ('attr', models.ForeignKey(on_delete=models.deletion.CASCADE, to='reports.Attr')),
            ],
            options={'db_table': 'mark_unknown_attr'},
        ),
        migrations.AlterField(model_name='marksafe', name='change_date', field=models.DateTimeField()),
        migrations.AlterField(model_name='markunknown', name='change_date', field=models.DateTimeField()),
        migrations.AlterField(model_name='markunsafe', name='change_date', field=models.DateTimeField()),
    ]