from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from oioioi.contests.menu import contest_admin_menu_registry
from oioioi.contests.utils import is_contest_admin

contest_admin_menu_registry.register(
    'plagiarism',
    _("Plagiarism tools"),
    lambda request: reverse('plagiarism'),
    is_contest_admin,
    order=100,
)
