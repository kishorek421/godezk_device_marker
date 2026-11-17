from django.db import migrations


def normalize(apps, schema_editor):
    Label = apps.get_model('studio', 'Label')
    for label in Label.objects.all():
        if any(isinstance(a, str) for a in label.attributes or []):
            label.attributes = [{'key': a, 'options': None} if isinstance(a, str) else a for a in label.attributes]
            label.save(update_fields=['attributes'])


def denormalize(apps, schema_editor):
    Label = apps.get_model('studio', 'Label')
    for label in Label.objects.all():
        if any(isinstance(a, dict) for a in label.attributes or []):
            label.attributes = [a['key'] if isinstance(a, dict) else a for a in label.attributes]
            label.save(update_fields=['attributes'])


class Migration(migrations.Migration):

    dependencies = [
        ('studio', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(normalize, denormalize),
    ]
