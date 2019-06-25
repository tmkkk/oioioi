# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2019-06-16 02:42
from __future__ import unicode_literals

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('problems', '0016_visibility_part3'),
    ]

    operations = [
        migrations.DeleteModel(
            name='OriginTag',
        ),
        migrations.DeleteModel(
            name='OriginTagThrough',
        ),
        migrations.CreateModel(
            name='OriginInfoCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="Type of information within this category. Short, searchable name consisting of only lowercase letters, numbers, and hyphens.<br>Examples: 'year', 'edition', 'stage', 'day'.", max_length=20, validators=(django.core.validators.RegexValidator(b'^[0-9a-z-]*$', 'Enter a valid name consisting only of lowercase letters, numbers, and hyphens.'),), verbose_name='name')),
                ('order', models.IntegerField(blank=True, help_text="Order used for grouping - should roughly reflect this category's specificity, e.g. 'year' should have lower order than 'round'.<br>Left blank means 'infinity', which means that this category will not be used for grouping.", null=True, verbose_name='grouping order')),
            ],
            options={
                'verbose_name': 'origin tag - information category',
                'verbose_name_plural': 'origin tags - information categories',
            },
        ),
        migrations.CreateModel(
            name='OriginInfoCategoryLocalization',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language', models.CharField(choices=[(b'en', b'English'), (b'pl', b'Polish')], max_length=2, verbose_name='language')),
                ('full_name', models.CharField(help_text='Human-readable name.', max_length=32, verbose_name='name translation')),
                ('origin_info_category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='localizations', to='problems.OriginInfoCategory')),
            ],
            options={
                'verbose_name': 'origin info category localization',
                'verbose_name_plural': 'origin info category localizations',
            },
        ),
        migrations.CreateModel(
            name='OriginInfoValue',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.CharField(help_text="Short, searchable value consisting of only lowercase letters and numbers.<br>This will be displayed verbatim in the Problemset - it must be unique within its parent tag.<br>Examples: for year: '2011', but for round: 'r1' (just '1' for round would be ambiguous).", max_length=32, validators=(django.core.validators.RegexValidator(b'^[0-9a-z-]*$', 'Enter a valid name consisting only of lowercase letters, numbers, and hyphens.'),), verbose_name='value')),
                ('order', models.IntegerField(default=0, help_text='Order in which this value will be sorted within its category.', verbose_name='display order')),
                ('category', models.ForeignKey(help_text='This information should be categorized under the selected category.', on_delete=django.db.models.deletion.CASCADE, related_name='values', to='problems.OriginInfoCategory', verbose_name='category')),
            ],
            options={
                'verbose_name': 'origin tag - information value',
                'verbose_name_plural': 'origin tags - information values',
            },
        ),
        migrations.CreateModel(
            name='OriginInfoValueLocalization',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language', models.CharField(choices=[(b'en', b'English'), (b'pl', b'Polish')], max_length=2, verbose_name='language')),
                ('full_value', models.CharField(help_text='Human-readable value.', max_length=64, verbose_name='translated value')),
                ('origin_info_value', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='localizations', to='problems.OriginInfoValue')),
            ],
            options={
                'verbose_name': 'origin info value localization',
                'verbose_name_plural': 'origin info value localizations',
            },
        ),
        migrations.CreateModel(
            name='OriginTag',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Short, searchable name consisting only of lowercase letters, numbers, and hyphens.<br>This will be displayed verbatim in the Problemset.', max_length=20, validators=(django.core.validators.RegexValidator(b'^[0-9a-z-]*$', 'Enter a valid name consisting only of lowercase letters, numbers, and hyphens.'),), verbose_name='name')),
                ('problems', models.ManyToManyField(blank=True, help_text='Selected problems will be tagged with this tag.<br>', to=b'problems.Problem', verbose_name='problems')),
            ],
            options={
                'verbose_name': 'origin tag',
                'verbose_name_plural': 'origin tags',
            },
        ),
        migrations.CreateModel(
            name='OriginTagLocalization',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language', models.CharField(choices=[(b'en', b'English'), (b'pl', b'Polish')], max_length=2, verbose_name='language')),
                ('full_name', models.CharField(help_text='Full, official name of the contest, competition, programming camp, etc. which this tag represents.', max_length=255, verbose_name='full name')),
                ('short_name', models.CharField(blank=True, help_text='(optional) Official abbreviation of the full name.', max_length=32, verbose_name='abbreviation')),
                ('description', models.TextField(blank=True, help_text='(optional) Longer description which Will be displayed in the Task Archive next to the name.', verbose_name='description')),
                ('origin_tag', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='localizations', to='problems.OriginTag')),
            ],
            options={
                'verbose_name': 'origin tag localization',
                'verbose_name_plural': 'origin tag localizations',
            },
        ),
        migrations.AddField(
            model_name='origininfovalue',
            name='parent_tag',
            field=models.ForeignKey(help_text='This information will be a possible additional information for problems tagged with the selected tag.', on_delete=django.db.models.deletion.CASCADE, related_name='info_values', to='problems.OriginTag', verbose_name='parent tag'),
        ),
        migrations.AddField(
            model_name='origininfovalue',
            name='problems',
            field=models.ManyToManyField(blank=True, help_text='Select problems described by this value. They will also be tagged with the parent tag.<br>', to=b'problems.Problem', verbose_name='problems'),
        ),
        migrations.AddField(
            model_name='origininfocategory',
            name='parent_tag',
            field=models.ForeignKey(help_text='This category will be a possible category of information for problems tagged with the selected tag.', on_delete=django.db.models.deletion.CASCADE, related_name='info_categories', to='problems.OriginTag', verbose_name='parent tag'),
        ),
        migrations.AlterUniqueTogether(
            name='origintaglocalization',
            unique_together=set([('origin_tag', 'language')]),
        ),
        migrations.AlterUniqueTogether(
            name='origininfovaluelocalization',
            unique_together=set([('origin_info_value', 'language')]),
        ),
        migrations.AlterUniqueTogether(
            name='origininfovalue',
            unique_together=set([('parent_tag', 'value')]),
        ),
        migrations.AlterUniqueTogether(
            name='origininfocategorylocalization',
            unique_together=set([('origin_info_category', 'language')]),
        ),
        migrations.AlterUniqueTogether(
            name='origininfocategory',
            unique_together=set([('name', 'parent_tag')]),
        ),
    ]