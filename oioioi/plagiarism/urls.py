from django.conf.urls import url

from oioioi.plagiarism import views

app_name = 'plagiarism'

contest_patterns = [
    url(
        r'^moss_export/$',
        views.MossExportView.as_view(),
        name='moss_export',
    ),
]
