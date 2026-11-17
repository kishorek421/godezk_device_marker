from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('studio', '0002_attribute_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='geometry',
            field=models.CharField(choices=[('any', 'Any'), ('box', 'Box'), ('polygon', 'Polygon'), ('point', 'Point')], default='any', max_length=8),
        ),
    ]
