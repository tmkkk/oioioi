from django.template.response import TemplateResponse
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.shortcuts import redirect

from oioioi.base.permissions import enforce_condition
from oioioi.contests.utils import contest_exists, is_contest_admin
from oioioi.exportszu.utils import SubmissionsWithUserDataCollector

from oioioi.plagiarism.forms import MossExportForm
from oioioi.plagiarism.utils import MossClient, submit_and_get_url


class MossExportView(View):
    form_class = MossExportForm

    def get(self, request):
        form = self.form_class(request)
        return TemplateResponse(request, 'plagiarism/moss_export.html', {'form': form})

    def post(self, request):
        form = self.form_class(request, request.POST)
        if form.is_valid():
            problem_instance = form.cleaned_data['problem_instance']
            language = form.cleaned_data['language']
            only_final = form.cleaned_data['only_final']
            userid = form.cleaned_data['userid']
            collector = SubmissionsWithUserDataCollector(
                request.contest,
                problem_instance=problem_instance,
                language=language,
                only_final=only_final,
            )
            client = MossClient(userid, language)
            url = submit_and_get_url(client, collector)
            return redirect(url)
        return TemplateResponse(request, 'plagiarism/moss_export.html', {'form': form})

    @method_decorator(enforce_condition(contest_exists & is_contest_admin))
    def dispatch(self, *args, **kwargs):
        return super(MossExportView, self).dispatch(*args, **kwargs)
