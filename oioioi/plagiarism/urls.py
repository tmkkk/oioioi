from django.conf.urls import url

from oioioi.plagiarism import views

app_name = 'plagiarism'

contest_patterns = [
    url(
        r'^plagiarism/$',
        views.MossExportView.as_view(),
        name='plagiarism',
    ),
]
