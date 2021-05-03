from django import forms
from django.conf import settings
from django.utils.translation import ugettext_lazy as _

SUBMITTABLE_LANGUAGES = getattr(settings, 'SUBMITTABLE_LANGUAGES', {})


class MossExportForm(forms.Form):
    problem_instance = forms.ModelChoiceField(
        queryset=None,
        label=_("Choose problem"),
        required=True,
    )
    language = forms.ChoiceField(
        choices=[
            (lang, d['display_name']) for lang, d in SUBMITTABLE_LANGUAGES.items()
        ],
        label=_("Programming language"),
        required=True,
    )
    only_final = forms.BooleanField(
        label=_("Only final submissions"), required=False, initial=True
    )
    userid = forms.IntegerField(
        label=_("MOSS user ID"),
        required=True,
        min_value=0,
        max_value=2 ** 32,
    )

    def __init__(self, request, *args, **kwargs):
        super(MossExportForm, self).__init__(*args, **kwargs)
        self.fields['problem_instance'].queryset = request.contest.probleminstance_set
