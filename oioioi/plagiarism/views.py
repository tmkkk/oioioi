from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views import View

from oioioi.base.permissions import enforce_condition
from oioioi.contests.utils import contest_exists, is_contest_admin

class MossExportView(View):
    def get(self, request):
        return TemplateResponse(request, 'plagiarism/moss_export.html')
    
    @method_decorator(enforce_condition(contest_exists & is_contest_admin))
    def dispatch(self, *args, **kwargs):
        return super(MossExportView, self).dispatch(*args, **kwargs)