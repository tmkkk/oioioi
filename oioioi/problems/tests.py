# coding: utf-8

import os.path
import urllib
from json import loads
from datetime import datetime  # pylint: disable=E0611

from django import forms
from django.contrib.auth.models import Permission, User, AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.urlresolvers import reverse
from django.db import transaction
from django.http import HttpResponse
from django.test import TransactionTestCase, RequestFactory
from django.test.utils import override_settings
from django.utils.timezone import utc
import six.moves.urllib.parse
from six.moves import range

from oioioi.base.tests import TestCase, check_not_accessible, \
        needs_linux
from oioioi.contests.current_contest import ContestMode
from oioioi.contests.handlers import update_problem_statistics
from oioioi.contests.models import Contest, ProblemInstance, Round, Submission
from oioioi.filetracker.tests import TestStreamingMixin
from oioioi.problems.controllers import ProblemController
from oioioi.problems.management.commands import recalculate_statistics
from oioioi.problems.models import (Problem, ProblemAttachment, ProblemPackage,
                                    ProblemStatistics, make_problem_filename,
                                    ProblemSite, ProblemStatement, OriginTag,
                                    OriginTagThrough, make_problem_filename)
from oioioi.problems.package import ProblemPackageBackend
from oioioi.problems.problem_site import problem_site_tab
from oioioi.problems.problem_sources import UploadedPackageSource
from oioioi.programs.controllers import ProgrammingContestController


class TestProblemController(ProblemController):
    __test__ = False
    def fill_evaluation_environ(self, environ, submission, **kwargs):
        raise NotImplementedError


class TestModels(TestCase):
    def test_problem_controller_property(self):
        problem = Problem(
            controller_name='oioioi.problems.tests.TestProblemController'
        )
        self.assert_(isinstance(problem.controller, TestProblemController))

    def test_make_problem_filename(self):
        p12 = Problem(pk=12)
        self.assertEqual(make_problem_filename(p12, 'a/hej.txt'),
                'problems/12/hej.txt')
        ps = ProblemStatement(pk=22, problem=p12)
        self.assertEqual(make_problem_filename(ps, 'a/hej.txt'),
                'problems/12/hej.txt')


class TestProblemViews(TestCase, TestStreamingMixin):
    fixtures = ['test_users', 'test_contest', 'test_full_package',
            'test_problem_instance', 'test_permissions']

    def test_problem_statement_view(self):
        # superuser
        self.assertTrue(self.client.login(username='test_admin'))
        statement = ProblemStatement.objects.get()

        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('show_statement', kwargs={'statement_id': statement.id})

        response = self.client.get(url)
        content = self.streamingContent(response)
        self.assertTrue(content.startswith('%PDF'))
        # contest admin
        self.assertTrue(self.client.login(username='test_contest_admin'))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = self.streamingContent(response)
        self.assertTrue(content.startswith('%PDF'))

        self.assertTrue(self.client.login(username='test_user'))
        response = self.client.get(url)
        self.assertIn(response.status_code, (403, 404))

    def test_admin_changelist_view(self):
        self.assertTrue(self.client.login(username='test_admin'))

        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:problems_problem_changelist')

        response = self.client.get(url)
        self.assertContains(response, 'Sum')

        self.assertTrue(self.client.login(username='test_user'))
        check_not_accessible(self, url)

        user = User.objects.get(username='test_user')
        content_type = ContentType.objects.get_for_model(Problem)
        permission = Permission.objects.get(content_type=content_type,
                                            codename='problems_db_admin')
        user.user_permissions.add(permission)
        response = self.client.get(url)
        self.assertContains(response, 'Sum')

    def test_admin_change_view(self):
        self.assertTrue(self.client.login(username='test_admin'))
        problem = Problem.objects.get()

        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:problems_problem_change',
                args=(problem.id,))

        response = self.client.get(url)
        elements_to_find = ['Sum', 'sum']
        for element in elements_to_find:
            self.assertIn(element, response.content)

    def test_admin_delete_view(self):
        self.assertTrue(self.client.login(username='test_admin'))
        problem = Problem.objects.get()
        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:problems_problem_delete',
                args=(problem.id,))

        self.client.post(url, {'post': 'yes'})
        self.assertEqual(Problem.objects.count(), 0)

    def _test_problem_permissions(self):
        problem = Problem.objects.get()
        contest = Contest.objects.get()
        statement = ProblemStatement.objects.get()
        check_not_accessible(self, 'oioioiadmin:problems_problem_add',
                data={'package_file': open(__file__, 'rb'),
                      'contest_id': contest.id})
        check_not_accessible(self, 'add_or_update_problem',
                kwargs={'contest_id': contest.id}, qs={'problem': problem.id})
        check_not_accessible(self, 'oioioiadmin:problems_problem_download',
                args=(problem.id,))
        check_not_accessible(self, 'oioioiadmin:problems_problem_change',
                args=(problem.id,))
        check_not_accessible(self, 'oioioiadmin:problems_problem_delete',
                args=(problem.id,))
        check_not_accessible(self, 'show_statement',
                kwargs={'statement_id': statement.id})

    def test_problem_permissions(self):
        self._test_problem_permissions()
        self.assertTrue(self.client.login(username='test_user'))
        self._test_problem_permissions()


class DummyPackageException(Exception):
    pass


class DummyPackageBackend(ProblemPackageBackend):
    description = "Dummy Package"

    def identify(self, path, original_filename=None):
        return True

    def get_short_name(self, path, original_filename=None):
        return 'bar'

    def unpack(self, env):
        pp = ProblemPackage.objects.get(id=env['package_id'])
        p = Problem.create(
            name='foo',
            short_name='bar',
            controller_name='oioioi.problems.controllers.ProblemController'
        )
        env['problem_id'] = p.id
        if 'FAIL' in pp.package_file.name:
            raise DummyPackageException("DUMMY_FAILURE")
        return env

    def pack(self, problem):
        return None


def dummy_handler(env):
    pp = ProblemPackage.objects.get(id=env['package_id'])
    if env.get('cc_rulez', False):
        pp.problem_name = 'contest_controller_rulez'
    else:
        pp.problem_name = 'handled'
    pp.save()
    return env


class DummySource(UploadedPackageSource):
    def create_env(self, *args, **kwargs):
        env = super(DummySource, self).create_env(*args, **kwargs)
        env['post_upload_handlers'] += ['oioioi.problems.tests.dummy_handler']
        return env


class DummyContestController(ProgrammingContestController):
    def adjust_upload_form(self, request, existing_problem, form):
        form.fields['cc_rulez'] = forms.BooleanField()

    def fill_upload_environ(self, request, form, env):
        env['cc_rulez'] = form.cleaned_data['cc_rulez']
        env['post_upload_handlers'] += ['oioioi.problems.tests.dummy_handler']


@override_settings(
    PROBLEM_PACKAGE_BACKENDS=('oioioi.problems.tests.DummyPackageBackend',)
)
class TestProblemUpload(TransactionTestCase):
    fixtures = ['test_users', 'test_contest']

    def test_successful_upload(self):
        ProblemInstance.objects.all().delete()
        contest = Contest.objects.get()
        self.assertTrue(self.client.login(username='test_admin'))
        data = {'package_file': ContentFile('eloziom', name='foo')}
        url = reverse('add_or_update_problem',
                      kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({'key': 'upload'})
        response = self.client.post(url, data, follow=True)
        self.assertIn('Package information', response.content)
        self.assertIn('Edit problem', response.content)
        self.assertNotIn('Error details', response.content)
        self.assertNotIn('Model solutions', response.content)
        package = ProblemPackage.objects.get()
        self.assertEqual(package.status, 'OK')
        self.assertEqual(package.problem_name, 'bar')
        problem = Problem.objects.get()
        self.assertEqual(problem.short_name, 'bar')
        problem_instance = ProblemInstance.objects \
            .filter(contest__isnull=False).get()
        self.assertEqual(problem_instance.contest, contest)
        self.assertEqual(problem_instance.problem, problem)

    def test_failed_upload(self):
        ProblemInstance.objects.all().delete()
        contest = Contest.objects.get()
        self.assertTrue(self.client.login(username='test_admin'))
        data = {'package_file': ContentFile('eloziom', name='FAIL')}
        url = reverse('add_or_update_problem',
                      kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({'key': 'upload'})
        response = self.client.post(url, data, follow=True)
        self.assertIn('DUMMY_FAILURE', response.content)
        self.assertIn('Error details', response.content)
        self.assertNotIn('Edit problem', response.content)
        self.assertNotIn('Model solutions', response.content)
        package = ProblemPackage.objects.get()
        self.assertEqual(package.problem_name, 'bar')
        self.assertEqual(package.status, 'ERR')
        problems = Problem.objects.all()
        self.assertEqual(len(problems), 0)
        problem_instances = ProblemInstance.objects.all()
        self.assertEqual(len(problem_instances), 0)

    @override_settings(
        PROBLEM_SOURCES=('oioioi.problems.tests.DummySource',)
    )
    def test_handlers(self):
        contest = Contest.objects.get()
        self.assertTrue(self.client.login(username='test_admin'))
        data = {'package_file': ContentFile('eloziom', name='foo')}
        url = reverse('add_or_update_problem',
                      kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({'key': 'upload'})
        response = self.client.post(url, data, follow=True)
        self.assertIn('Package information', response.content)
        package = ProblemPackage.objects.get()
        self.assertEqual(package.status, 'OK')
        self.assertEqual(package.problem_name, 'handled')

    def test_contest_controller_plugins(self):
        contest = Contest.objects.get()
        contest.controller_name = \
                'oioioi.problems.tests.DummyContestController'
        contest.save()

        self.assertTrue(self.client.login(username='test_admin'))
        data = {'package_file': ContentFile('eloziom', name='foo'),
                'cc_rulez': True}
        url = reverse('add_or_update_problem',
                      kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({'key': 'upload'})
        response = self.client.post(url, data, follow=True)
        self.assertIn('Package information', response.content)
        package = ProblemPackage.objects.get()
        self.assertEqual(package.status, 'OK')
        self.assertEqual(package.problem_name, 'contest_controller_rulez')

    def test_problem_submission_limit_changed(self):
        contest = Contest.objects.get()
        package_file = ContentFile('eloziom', name='foo')
        self.assertTrue(self.client.login(username='test_admin'))
        url = reverse('oioioiadmin:problems_problem_add')
        response = self.client.get(url, {'contest_id': contest.id},
                follow=True)
        url = response.redirect_chain[-1][0]
        self.assertEqual(response.status_code, 200)

        response = self.client.post(url,
                {'package_file': package_file}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Problem.objects.count(), 1)
        self.assertEqual(ProblemInstance.objects.count(), 2)

        problem = ProblemInstance.objects \
            .filter(contest__isnull=False).get().problem
        contest.default_submissions_limit += 100
        contest.save()

        url = reverse('add_or_update_problem',
                kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({
                                'problem': problem.id})
        response = self.client.get(url, follow=True)
        url = response.redirect_chain[-1][0]
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url,
                {'package_file': package_file}, follow=True)
        self.assertEqual(response.status_code, 200)

        pis = ProblemInstance.objects.filter(problem=problem)
        self.assertEqual(pis.count(), 2)

        pi = ProblemInstance.objects.get(contest__isnull=False)
        self.assertEqual(pi.submissions_limit,
                         contest.default_submissions_limit - 100)


class TestProblemPackageAdminView(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_problem_packages',
            'test_problem_instance', 'test_two_empty_contests']

    def test_links(self):
        self.assertTrue(self.client.login(username='test_admin'))

        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:problems_problempackage_changelist')

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Error details', response.content)
        self.assertIn('Edit problem', response.content)
        self.assertIn('Model solutions', response.content)

        self.client.get('/c/c1/')  # 'c1' becomes the current contest
        url = reverse('oioioiadmin:problems_problempackage_changelist')

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Error details', response.content)
        # Not visible, because the problem's contest is 'c', not 'c1'
        self.assertNotIn('Edit problem', response.content)
        # Not visible, because the problem instances's contest is 'c', not 'c1'
        self.assertNotIn('Model solutions', response.content)


class TestProblemPackageViews(TestCase, TestStreamingMixin):
    fixtures = ['test_users', 'test_contest', 'test_problem_packages',
            'test_problem_instance']

    def _test_package_permissions(self, is_admin=False):
        models = ['problempackage', 'contestproblempackage']
        view_prefix = 'oioioiadmin:problems_'
        package = ProblemPackage.objects.get(pk=2)
        for m in models:
            prefix = view_prefix + m + '_'
            check_not_accessible(self, prefix + 'add')
            check_not_accessible(self, prefix + 'change', args=(package.id,))
            if not is_admin:
                check_not_accessible(self, prefix + 'delete',
                        args=(package.id,))
        if not is_admin:
            check_not_accessible(self, 'download_package', args=(package.id,))
            check_not_accessible(self, 'download_package_traceback',
                                       kwargs={'package_id': str(package.id)})

    def test_admin_changelist_view(self):
        self.assertTrue(self.client.login(username='test_admin'))

        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('oioioiadmin:problems_problempackage_changelist')

        response = self.client.get(url)
        self.assertContains(response, 'XYZ')

    def test_package_file_view(self):
        package = ProblemPackage.objects.get(pk=1)
        package.package_file = ContentFile('eloziom', name='foo')
        package.save()
        self.assertTrue(self.client.login(username='test_admin'))

        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('download_package',
                      kwargs={'package_id': str(package.id)})

        response = self.client.get(url)
        content = self.streamingContent(response)
        self.assertEqual(content, 'eloziom')

    def test_package_traceback_view(self):
        package = ProblemPackage.objects.get(pk=2)
        package.traceback = ContentFile('eloziom', name='foo')
        package.save()
        self.assertTrue(self.client.login(username='test_admin'))
        self.client.get('/c/c/')  # 'c' becomes the current contest
        url = reverse('download_package_traceback',
                      kwargs={'package_id': str(package.id)})

        response = self.client.get(url)
        content = self.streamingContent(response)
        self.assertEqual(content, 'eloziom')

        package.traceback = None
        package.save()
        self.assertTrue(self.client.login(username='test_admin'))
        url = reverse('download_package_traceback',
                      kwargs={'package_id': str(package.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_package_permissions(self):
        self._test_package_permissions()
        self.assertTrue(self.client.login(username='test_user'))
        self._test_package_permissions()
        self.assertTrue(self.client.login(username='test_admin'))
        self._test_package_permissions(is_admin=True)


@override_settings(CONTEST_MODE=ContestMode.neutral)
class TestProblemSite(TestCase, TestStreamingMixin):
    fixtures = ['test_users', 'test_contest', 'test_full_package',
            'test_problem_instance', 'test_submission', 'test_problem_site']

    def _get_site_urls(self):
        url = reverse('problem_site', kwargs={'site_key': '123'})
        url_statement = url + "?key=statement"
        url_files = url + "?key=files"
        url_submissions = url + "?key=submissions"
        return {'site': url,
                'statement': url_statement,
                'files': url_files,
                'submissions': url_submissions}

    def _create_PA(self):
        problem = Problem.objects.get()
        pa = ProblemAttachment(problem=problem,
                description='problem-attachment',
                content=ContentFile('content-of-probatt', name='probatt.txt'))
        pa.save()

    def test_default_tabs(self):
        urls = self._get_site_urls()
        response = self.client.get(urls['site'])
        self.assertRedirects(response, urls['statement'])
        response = self.client.get(urls['statement'])
        for url in urls.values():
            self.assertContains(response, url)

    def test_statement_tab(self):
        url_external_stmt = reverse('problem_site_external_statement',
                kwargs={'site_key': '123'})
        response = self.client.get(self._get_site_urls()['statement'])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, url_external_stmt)

    def test_files_tab(self):
        url = self._get_site_urls()['files']
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count('<tr'), 0)

        self._create_PA()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count('<tr'), 2)
        url_attachment = reverse('problem_site_external_attachment',
                kwargs={'site_key': '123', 'attachment_id': 1})
        self.assertContains(response, url_attachment)

    def test_submissions_tab(self):
        for problem in Problem.objects.all():
            problem.main_problem_instance.contest = None
            problem.main_problem_instance.round = None
            problem.main_problem_instance.save()

        url = self._get_site_urls()['submissions']
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count('<tr'), 0)
        self.assertTrue(self.client.login(username='test_user'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(url)
        self.assertEqual(response.content.count('<tr'), 3)

    def test_add_new_tab(self):
        tab_title = 'Test tab'
        tab_contents = 'Hello from test tab'

        @problem_site_tab(tab_title, key='testtab')
        def problem_site_test(request, problem):
            return HttpResponse(tab_contents)

        url = self._get_site_urls()['site'] + '?key=testtab'
        response = self.client.get(url)
        self.assertContains(response, tab_title)
        self.assertContains(response, tab_contents)

    def test_external_statement_view(self):
        url_external_stmt = reverse('problem_site_external_statement',
                kwargs={'site_key': '123'})
        response = self.client.get(url_external_stmt)
        self.assertEqual(response.status_code, 200)
        content = self.streamingContent(response)
        self.assertTrue(content.startswith('%PDF'))

    def test_external_attachment_view(self):
        self._create_PA()
        url_external_attmt = reverse('problem_site_external_attachment',
                kwargs={'site_key': '123', 'attachment_id': 1})
        response = self.client.get(url_external_attmt)
        self.assertStreamingEqual(response, 'content-of-probatt')


class TestProblemsetPage(TestCase):
    fixtures = ['test_users', 'test_problemset_author_problems',
            'test_contest']

    def test_problemlist(self):
        self.assertTrue(self.client.login(username='test_user'))
        url = reverse('problemset_main')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        public_problems = Problem.objects.filter(is_public=True)
        for problem in public_problems:
            self.assertIn(str(problem.name), str(response.content))
        # User with no administered contests doesn't see the button
        self.assertEqual(response.content.count('Add to contest'), 0)

        url = reverse('problemset_my_problems')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        author_user = User.objects.filter(username='test_user')
        author_problems = Problem.objects.filter(author=author_user)
        for problem in author_problems:
            self.assertIn(str(problem.name), str(response.content))
        # User with no administered contests doesn't see the button
        self.assertEqual(response.content.count("Add to contest"), 0)
        self.assertNotIn('All problems', str(response.content))

        url = reverse('problemset_all_problems')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 403)

        self.assertTrue(self.client.login(username='test_admin'))
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('All problems', str(response.content))
        # One link for problem site, another
        # for "More..." link in "Add to contest"
        self.assertEqual(response.content.count('/problemset/problem/'),
                         Problem.objects.count() * 2)
        self.assertEqual(response.content.count('Add to contest'),
                         Problem.objects.count())


def get_test_filename(name):
    return os.path.join(os.path.dirname(__file__), '../sinolpack/files', name)


@needs_linux
class TestProblemsetUploading(TransactionTestCase, TestStreamingMixin):
    fixtures = ['test_users', 'test_contest']

    def check_models_for_simple_package(self, problem_instance):
        url = reverse('model_solutions', args=[problem_instance.id])
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        to_find = ["0", "1a", "1b", "1c", "2"]
        for test in to_find:
            self.assertIn(">" + test + "</th>", response.content)

    def test_upload_problem(self):
        filename = get_test_filename('test_simple_package.zip')
        self.assertTrue(self.client.login(username='test_admin'))

        # add problem to problemset
        url = reverse('problemset_add_or_update')
        # not possible from problemset :)
        response = self.client.get(url, {'key': "problemset_source"},
                                   follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Option not available", response.content)
        self.assertIn("Add problem", response.content)
        self.assertNotIn("Select", response.content)
        # but ok by package
        response = self.client.get(url, follow=True)
        url = response.redirect_chain[-1][0]
        self.assertEqual(response.status_code, 200)
        self.assertIn("Add problem", response.content)
        self.assertIn('problems/problemset/add-or-update.html',
                [getattr(t, 'name', None) for t in response.templates])
        response = self.client.post(url,
                {'package_file': open(filename, 'rb')}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Problem.objects.count(), 1)
        self.assertEqual(ProblemInstance.objects.count(), 1)
        self.assertEqual(ProblemSite.objects.count(), 1)

        # problem is not visible in "Public"
        url = reverse('problemset_main')
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Testowe", response.content)
        self.assertNotIn("<td>tst</td>", response.content)
        # but visible in "My problems"
        url = reverse('problemset_my_problems')
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, follow=True)
        self.assertIn("Testowe", response.content)
        self.assertIn("<td>tst</td>", response.content)
        # and we are problem's author and problem_site exists
        problem = Problem.objects.get()
        url = reverse('problem_site', args=[problem.problemsite.url_key])
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Edit problem', response.content)
        self.assertIn('Reupload problem', response.content)
        self.assertIn('Show model solutions', response.content)
        # we can see model solutions of main_problem_instance
        self.check_models_for_simple_package(problem.main_problem_instance)

        # reuploading problem in problemset is not aviable from problemset
        url = reverse('problemset_add_or_update')
        response = self.client.get(url, {'key': "problemset_source",
                                         'problem': problem.id}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Option not available", response.content)
        self.assertIn("Update problem", response.content)
        self.assertNotIn("Select", response.content)

    def test_add_problem_to_contest(self):
        ProblemInstance.objects.all().delete()

        contest = Contest.objects.get()
        contest.default_submissions_limit = 42
        contest.save()
        filename = get_test_filename('test_simple_package.zip')
        self.assertTrue(self.client.login(username='test_admin'))
        # Add problem to problemset
        url = reverse('problemset_add_or_update')
        response = self.client.get(url, follow=True)
        url = response.redirect_chain[-1][0]
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url,
                {'package_file': open(filename, 'rb')}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Problem.objects.count(), 1)
        self.assertEqual(ProblemInstance.objects.count(), 1)

        problem = Problem.objects.get()
        url_key = problem.problemsite.url_key

        # now, add problem to the contest
        url = reverse('add_or_update_problem',
                kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({
                                'key': "problemset_source"})
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Add from Problemset', response.content)
        self.assertIn('Enter problem', response.content)
        self.assertIn('s secret key', response.content)
        self.assertIn('Choose problem from problemset', response.content)

        pi_number = 3
        for i in range(pi_number):
            url = reverse('add_or_update_problem',
                    kwargs={'contest_id': contest.id}) + '?' + \
                        six.moves.urllib.parse.urlencode({
                                'key': "problemset_source"})
            response = self.client.get(url,
                       {'url_key': url_key}, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn(str(url_key), response.content)
            response = self.client.post(url,
                        {'url_key': url_key}, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(ProblemInstance.objects.count(), 2 + i)

        # check submissions limit
        for pi in ProblemInstance.objects.filter(contest__isnull=False):
            self.assertEqual(pi.submissions_limit,
                             contest.default_submissions_limit)

        # add probleminstances to round
        with transaction.atomic():
            for pi in ProblemInstance.objects.filter(contest__isnull=False):
                pi.round = Round.objects.get()
                pi.save()

        # we can see model solutions
        pi = ProblemInstance.objects.filter(contest__isnull=False)[0]
        self.check_models_for_simple_package(pi)

        # tests and models of every problem_instance are independent
        num_tests = pi.test_set.count()
        for test in pi.test_set.all():
            test.delete()
        pi.save()

        url = reverse('model_solutions', args=[pi.id])
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        for test in ["0", "1a", "1b", "1c", "2"]:
            self.assertNotIn(">" + test + "</th>", response.content)

        for pi2 in ProblemInstance.objects.all():
            if pi2 != pi:
                self.assertEqual(pi2.test_set.count(), num_tests)
                self.check_models_for_simple_package(pi2)

        # reupload one ProblemInstance from problemset
        url = reverse('add_or_update_problem',
                kwargs={'contest_id': contest.id}) + '?' + \
                    six.moves.urllib.parse.urlencode({
                            'key': "problemset_source",
                            'problem': problem.id,
                            'instance_id': pi.id})
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(url_key), response.content)
        self.assertNotIn("Select", response.content)
        response = self.client.post(url, {'url_key': url_key}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProblemInstance.objects.count(), pi_number + 1)
        self.assertTrue(pi.round)
        self.assertEqual(pi.test_set.count(), num_tests)
        self.check_models_for_simple_package(pi)
        self.assertIn("1 PROBLEM NEEDS REJUDGING", response.content)
        self.assertEqual(response.content
               .count("Rejudge all submissions for problem"), 1)

        # reupload problem in problemset
        url = reverse('problemset_add_or_update') + '?' + \
                    six.moves.urllib.parse.urlencode({'problem': problem.id})
        response = self.client.get(url, follow=True)
        url = response.redirect_chain[-1][0]
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url,
                {'package_file': open(filename, 'rb')}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProblemInstance.objects.count(), pi_number + 1)
        self.assertIn("3 PROBLEMS NEED REJUDGING", response.content)
        self.check_models_for_simple_package(pi)

        # rejudge one problem
        url = reverse('rejudge_all_submissions_for_problem', args=[pi.id])
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("You are going to rejudge 1", response.content)
        response = self.client.post(url, {'submit': True}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content
                 .count("Rejudge all submissions for problem"), pi_number - 1)
        self.assertIn("1 rejudge request received.", response.content)

    def test_uploading_to_contest(self):
        # we can add problem directly from contest
        contest = Contest.objects.get()
        filename = get_test_filename('test_simple_package.zip')
        self.assertTrue(self.client.login(username='test_admin'))
        url = reverse('oioioiadmin:problems_problem_add')
        response = self.client.get(url, {'contest_id': contest.id},
                follow=True)
        url = response.redirect_chain[-1][0]
        self.assertEqual(response.status_code, 200)
        self.assertIn('problems/add-or-update.html',
                [getattr(t, 'name', None) for t in response.templates])
        response = self.client.post(url,
                {'package_file': open(filename, 'rb')}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Problem.objects.count(), 1)
        self.assertEqual(ProblemInstance.objects.count(), 2)

        # many times
        response = self.client.post(url,
                {'package_file': open(filename, 'rb')}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Problem.objects.count(), 2)
        self.assertEqual(ProblemInstance.objects.count(), 4)

        # and nothing needs rejudging
        self.assertNotIn('REJUDGING', response.content)


class TestTags(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_problem_packages',
                'test_problem_site', 'test_tags']

    def test_tag_hints_view(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_query_url(query):
            url = reverse('get_tag_hints')
            return url + '?' + six.moves.urllib.parse.urlencode({'substr': query})

        response = self.client.get(get_query_url('rowk'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('mrowkowiec', response.content)
        self.assertIn('mrowka', response.content)
        self.assertNotIn('XYZ', response.content)

        response = self.client.get(get_query_url('rowka'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('mrowkowiec', response.content)
        self.assertIn('mrowka', response.content)
        self.assertNotIn('XYZ', response.content)

        response = self.client.get(get_query_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('mrowkowiec', response.content)
        self.assertNotIn('mrowka', response.content)
        self.assertNotIn('XYZ', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_list_search_visible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url('mrowkowiec'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('mrowka'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_list_search_invisible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url('mrowkowiec'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('mrowka'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)


class TestAlgorithmTags(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_problem_packages',
                'test_problem_site', 'test_algorithmtags']

    def test_tag_hints_view(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_query_url(query):
            url = reverse('get_algorithmtag_hints')
            return url + '?' + six.moves.urllib.parse.urlencode({'substr': query})

        response = self.client.get(get_query_url('rowk'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('mrowkowiec', response.content)
        self.assertIn('mrowka', response.content)
        self.assertNotIn('XYZ', response.content)

        response = self.client.get(get_query_url('rowka'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('mrowkowiec', response.content)
        self.assertIn('mrowka', response.content)
        self.assertNotIn('XYZ', response.content)

        response = self.client.get(get_query_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('mrowkowiec', response.content)
        self.assertNotIn('mrowka', response.content)
        self.assertNotIn('XYZ', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_list_search_visible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url('mrowkowiec'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('mrowka'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_list_search_invisible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url('mrowkowiec'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('mrowka'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>mrowka<', response.content)


class TestDifficultyTags(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_problem_packages',
                'test_problem_site', 'test_tags', 'test_difficultytags']

    def test_tag_hints_view(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_query_url(query):
            url = reverse('get_difficultytag_hints')
            return url + '?' + six.moves.urllib.parse.urlencode({'substr': query})

        response = self.client.get(get_query_url('rud'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('trudne', response.content)
        self.assertNotIn('latwe', response.content)
        self.assertNotIn('XYZ', response.content)

        response = self.client.get(get_query_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('trudne', response.content)
        self.assertNotIn('latwe', response.content)
        self.assertNotIn('XYZ', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_list_search_visible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>trudne<', response.content)
        self.assertIn('>mrowkowiec<', response.content)
        self.assertNotIn('>latwe<', response.content)

        response = self.client.get(get_search_url('trudne'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>trudne<', response.content)
        self.assertIn('>mrowkowiec<', response.content)
        self.assertNotIn('>latwe<', response.content)

        response = self.client.get(get_search_url('latwe'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>trudne<', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>latwe<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>trudne<', response.content)
        self.assertNotIn('>mrowkowiec<', response.content)
        self.assertNotIn('>latwe<', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_list_search_invisible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>trudne<', response.content)
        self.assertNotIn('>latwe<', response.content)

        response = self.client.get(get_search_url('trudne'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>trudne<', response.content)
        self.assertNotIn('>latwe<', response.content)

        response = self.client.get(get_search_url('latwe'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>trudne<', response.content)
        self.assertNotIn('>latwe<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>trudne<', response.content)
        self.assertNotIn('>latwe<', response.content)


class TestOriginTags(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_problem_packages',
                'test_problem_site', 'test_tags', 'test_origintags']

    def test_adding_parent_tag(self):
        problem = Problem.objects.get(pk=1)
        leaf_tag = OriginTag.objects.get(pk=5)
        root_tag = OriginTag.objects.get(pk=4)
        OriginTagThrough.objects.create(problem=problem, tag=leaf_tag)
        self.assertTrue(OriginTagThrough.objects.filter(problem=problem,
            tag=root_tag).exists())

    def test_tag_hints_view(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_query_url(query):
            url = reverse('get_origintag_hints')
            return url + '?' + six.moves.urllib.parse.urlencode({'substr': query})

        response = self.client.get(get_query_url('etap'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('OI', response.content)
        self.assertIn('etap I', response.content)
        self.assertNotIn('PA', response.content)
        self.assertNotIn('XYZ', response.content)

        response = self.client.get(get_query_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('OI', response.content)
        self.assertNotIn('etap I', response.content)
        self.assertNotIn('PA', response.content)
        self.assertNotIn('XYZ', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_list_search_visible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>OI<', response.content)
        self.assertIn('>etap I<', response.content)
        self.assertNotIn('>PA<', response.content)

        response = self.client.get(get_search_url('OI'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertIn('>OI<', response.content)
        self.assertIn('>etap I<', response.content)
        self.assertNotIn('>PA<', response.content)

        response = self.client.get(get_search_url('PA'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>OI<', response.content)
        self.assertNotIn('>etap I<', response.content)
        self.assertNotIn('>PO<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>OI<', response.content)
        self.assertNotIn('>etap I<', response.content)
        self.assertNotIn('>PA<', response.content)

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_list_search_invisible(self):
        self.assertTrue(self.client.login(username='test_user'))
        self.client.get('/c/c/')  # 'c' becomes the current contest

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?' + six.moves.urllib.parse.urlencode({'q': query})

        response = self.client.get(get_search_url(''))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>OI<', response.content)
        self.assertNotIn('>PA<', response.content)

        response = self.client.get(get_search_url('OI'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('XYZ', response.content)
        self.assertNotIn('>OI<', response.content)
        self.assertNotIn('>PA<', response.content)

        response = self.client.get(get_search_url('PA'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>OI<', response.content)
        self.assertNotIn('>PA<', response.content)

        response = self.client.get(get_search_url('bad_tag'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('XYZ', response.content)
        self.assertNotIn('>OI<', response.content)
        self.assertNotIn('>PA<', response.content)


class TestSearch(TestCase):
    fixtures = ['test_users', 'test_problem_search']

    def test_search_hints_view(self):
        self.assertEqual(200, 200)
        self.assertTrue(self.client.login(username='test_user'))

        def get_query_url_public(query):
            url = reverse('get_search_hints', kwargs={'view_type': 'public'})
            return url + '?substr=' + query

        def get_query_url_my(query):
            url = reverse('get_search_hints', kwargs={'view_type': 'my'})
            return url + '?substr=' + query

        def get_query_url_all(query):
            url = reverse('get_search_hints', kwargs={'view_type': 'all'})
            return url + '?substr=' + query

        response = self.client.get(get_query_url_public('Spa'))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertIn(u'Ze spacj\u0105', loads(response.content))
        self.assertNotIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertNotIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_public(u'\u017b\u00f3\u0142'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertNotIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertNotIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_public('TAGUS'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertNotIn('Niepubliczny', loads(response.content))
        self.assertIn('tagus', loads(response.content))
        self.assertNotIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_public('niepu'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertNotIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertNotIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_all('niepu'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertNotIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_public('pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertNotIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_all('pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_my('pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertIn('publiczny', loads(response.content))

        response = self.client.get(get_query_url_my(u'\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', loads(response.content))
        self.assertNotIn(u'Ze spacj\u0105', loads(response.content))
        self.assertNotIn('Niepubliczny', loads(response.content))
        self.assertNotIn('tagus', loads(response.content))
        self.assertNotIn('publiczny', loads(response.content))

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_public_search_visible(self):
        self.assertTrue(self.client.login(username='test_user'))

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?q=' + query

        response = self.client.get(get_search_url('tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('nie'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'spacj\u0105'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('e spa'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('"e spa"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:\u017b\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:"Ze spacj\u0105"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:"Ze spacja"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:Ze spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_public_search_invisible(self):
        self.assertTrue(self.client.login(username='test_user'))

        def get_search_url(query):
            url = reverse('problemset_main')
            return url + '?q=' + query

        response = self.client.get(get_search_url('tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('nie'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'spacj\u0105'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('e spa'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('"e spa"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:\u017b\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:"Ze spacj\u0105"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:"Ze spacja"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:Ze spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_all_search_visible(self):
        self.assertTrue(self.client.login(username='test_admin'))

        def get_search_url(query):
            url = reverse('problemset_all_problems')
            return url + '?q=' + query

        response = self.client.get(get_search_url('tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('nie'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'spacj\u0105'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('e spa'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('"e spa"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:\u017b\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:"Ze spacj\u0105"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:"Ze spacja"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:Ze spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_all_search_invisible(self):
        self.assertTrue(self.client.login(username='test_admin'))

        def get_search_url(query):
            url = reverse('problemset_all_problems')
            return url + '?q=' + query

        response = self.client.get(get_search_url('tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('nie'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'spacj\u0105'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('e spa'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('"e spa"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:\u017b\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:"Ze spacj\u0105"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:"Ze spacja"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:Ze spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

    @override_settings(PROBLEM_TAGS_VISIBLE=True)
    def test_problemset_my_search_visible(self):
        self.assertTrue(self.client.login(username='test_user'))

        def get_search_url(query):
            url = reverse('problemset_my_problems')
            return url + '?q=' + query

        response = self.client.get(get_search_url('tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('nie'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'spacj\u0105'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('e spa'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('"e spa"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:"Ze spacj\u0105"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:"Ze spacja"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:Ze spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

    @override_settings(PROBLEM_TAGS_VISIBLE=False)
    def test_problemset_my_search_invisible(self):
        self.assertTrue(self.client.login(username='test_user'))

        def get_search_url(query):
            url = reverse('problemset_my_problems')
            return url + '?q=' + query

        response = self.client.get(get_search_url('tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('nie'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'spacj\u0105'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'\u017c\u00f3\u0142w tagus'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('e spa'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('"e spa"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:pub'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('tag:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:publiczny'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:zolw'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:\u017c\u00f3\u0142w'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('zolw spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url(u'name:"Ze spacj\u0105"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:"Ze spacja"'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertNotIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))

        response = self.client.get(get_search_url('name:Ze spacja'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(u'\u017b\u00f3\u0142w', response.content.decode('utf-8'))
        self.assertIn(u'Ze spacj\u0105', response.content.decode('utf-8'))
        self.assertNotIn('Niepubliczny', response.content.decode('utf-8'))
        self.assertNotIn('>tagus<', response.content.decode('utf-8'))
        self.assertNotIn('>publiczny<', response.content.decode('utf-8'))


class TestAddToProblemsetPermissions(TestCase):
    fixtures = ['test_users']

    @override_settings(EVERYBODY_CAN_ADD_TO_PROBLEMSET=False)
    def test_default_permissions(self):
        url_main = reverse('problemset_main')
        url_add = reverse('problemset_add_or_update')

        response = self.client.get(url_main, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Add problem', response.content)
        self.assertIn('Welcome to problemset, the place where all'
            ' the problems are.', response.content)
        response = self.client.get(url_add, follow=True)
        self.assertEqual(response.status_code, 403)

        self.assertTrue(self.client.login(username='test_admin'))
        response = self.client.get(url_main)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Add problem', response.content)
        response = self.client.get(url_add, follow=True)
        self.assertEqual(response.status_code, 200)

        self.assertTrue(self.client.login(username='test_user'))
        response = self.client.get(url_main)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Add problem', response.content)
        url_add = reverse('problemset_add_or_update')
        response = self.client.get(url_add, follow=True)
        self.assertEqual(response.status_code, 403)

    @override_settings(EVERYBODY_CAN_ADD_TO_PROBLEMSET=True)
    def test_everyone_allowed_permissions(self):
        url_main = reverse('problemset_main')
        url_add = reverse('problemset_add_or_update')

        response = self.client.get(url_main, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Add problem', response.content)
        self.assertIn('Welcome to problemset, the place where all'
            ' the problems are.', response.content)
        response = self.client.get(url_add, follow=True)
        self.assertEqual(response.status_code, 403)

        self.assertTrue(self.client.login(username='test_admin'))
        url_main = reverse('problemset_main')
        response = self.client.get(url_main)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Add problem', response.content)
        url_add = reverse('problemset_add_or_update')
        response = self.client.get(url_add, follow=True)
        self.assertEqual(response.status_code, 200)

        self.assertTrue(self.client.login(username='test_user'))
        response = self.client.get(url_main)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Add problem', response.content)
        url_add = reverse('problemset_add_or_update')
        response = self.client.get(url_add, follow=True)
        self.assertEqual(response.status_code, 200)


class TestAddToContestFromProblemset(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_full_package',
            'test_problem_instance', 'test_submission', 'test_problem_site']

    def test_add_from_problemlist(self):
        self.assertTrue(self.client.login(username='test_admin'))
        # Visit contest page to register it in recent contests
        contest = Contest.objects.get()
        self.client.get('/c/%s/dashboard/' % contest.id)
        url = reverse('problemset_all_problems')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('All problems', str(response.content))
        # One link for problem site, another
        # for "More..." link in "Add to contest"
        self.assertEqual(response.content.count('/problemset/problem/'),
                         Problem.objects.count() * 2)
        self.assertEqual(response.content.count('Add to contest'),
                         Problem.objects.count())
        self.assertIn('data-addorupdate', str(response.content))
        self.assertIn('data-urlkey', str(response.content))
        self.assertIn('add_to_contest', str(response.content))

    def test_add_from_problemsite(self):
        self.assertTrue(self.client.login(username='test_admin'))
        contest = Contest.objects.get()
        self.client.get('/c/%s/dashboard/' % contest.id)
        url = reverse('problem_site', kwargs={'site_key': '123'})
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count('Add to contest'), 1)
        self.assertIn('data-addorupdate', str(response.content))
        self.assertIn('data-urlkey', str(response.content))
        self.assertIn('add_to_contest', str(response.content))
        self.assertIn('123', str(response.content))

    def test_add_from_selectcontest(self):
        contest2 = Contest(id='c2', name='Contest2',
            controller_name='oioioi.contests.tests.PrivateContestController')
        contest2.save()
        contest2.creation_date = datetime(2002, 1, 1, tzinfo=utc)
        contest2.save()
        contest3 = Contest(id='c3', name='Contest3',
            controller_name='oioioi.contests.tests.PrivateContestController')
        contest3.save()
        contest3.creation_date = datetime(2004, 1, 1, tzinfo=utc)
        contest3.save()
        contest4 = Contest(id='c4', name='Contest4',
            controller_name='oioioi.contests.tests.PrivateContestController')
        contest4.save()
        contest4.creation_date = datetime(2003, 1, 1, tzinfo=utc)
        contest4.save()

        self.assertTrue(self.client.login(username='test_admin'))
        # Now we're not having any contest in recent contests.
        # As we are contest administrator, the button should still appear.
        url = reverse('problemset_all_problems')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('All problems', str(response.content))
        self.assertEqual(response.content.count('/problemset/problem/'),
                         Problem.objects.count() * 2)
        self.assertEqual(response.content.count('Add to contest'),
                         Problem.objects.count())
        # But it shouldn't be able to fill the form
        self.assertNotIn('data-addorupdate', str(response.content))
        self.assertNotIn('data-urlkey', str(response.content))
        # And it should point to select_contest page
        self.assertIn('/problem/123/add_to_contest/?problem_name=sum',
            str(response.content))
        # Follow the link...
        url = reverse('problemset_add_to_contest', kwargs={'site_key': '123'})
        url += '?problem_name=sum'
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('to add the <code>sum</code> problem to',
                      str(response.content))
        # This time we should be able to fill the form
        self.assertIn('data-addorupdate', str(response.content))
        self.assertIn('data-urlkey', str(response.content))
        self.assertIn('add_to_contest', str(response.content))
        self.assertIn('123', str(response.content))
        self.assertEqual(len(response.context['administered_contests']), 4)
        self.assertEquals(list(response.context['administered_contests']),
            list(Contest.objects.order_by('-creation_date').all()))
        self.assertContains(response, 'Contest2', count=1)
        self.assertContains(response, 'Contest3', count=1)
        self.assertContains(response, 'Contest4', count=1)
        self.assertLess(response.content.index('Contest3'),
            response.content.index('Contest4'))
        self.assertLess(response.content.index('Contest4'),
            response.content.index('Contest2'))


def get_submission_left(username, contest_id='c', pi_pk=1):
    request = RequestFactory().request()
    request.user = User.objects.get(username=username) \
        if username is not None else AnonymousUser()

    if contest_id is not None:
        request.contest = Contest.objects.get(id=contest_id)
    problem_instance = ProblemInstance.objects.get(pk=pi_pk)
    return problem_instance.controller.get_submissions_left(request,
                                                            problem_instance)


class TestSubmissionLeft(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_full_package',
                'test_problem_instance', 'test_submission']

    def test_admin(self):
        assert get_submission_left('test_admin') is None

    def test_user_without_submissions(self):
        assert get_submission_left('test_user2') == 10

    def test_user_with_submissions(self):
        assert get_submission_left('test_user') == 9

    def test_not_authenticated_user(self):
        assert get_submission_left(None) is None


class TestSubmissionLeftWhenNoLimit(TestCase):
    fixtures = ['test_users', 'test_contest', 'test_full_package',
                'test_problem_instance_with_no_submissions_limit',
                'test_submission']

    def test_admin(self):
        assert get_submission_left('test_admin') is None

    def test_user_without_submissions(self):
        assert get_submission_left('test_user2') is None

    def test_user_with_submissions(self):
        assert get_submission_left('test_user') is None

    def test_not_authenticated_user(self):
        assert get_submission_left(None) is None


class TestSubmissionLeftWhenNoContest(TestCase):
    fixtures = ['test_users', 'test_full_package',
                'test_problem_instance_with_no_contest']

    def test_admin(self):
        assert get_submission_left('test_admin', None) is None

    def test_user_without_submissions(self):
        assert get_submission_left('test_user', None) is None

    def test_not_authenticated_user(self):
        assert get_submission_left(None, None) is None


@override_settings(PROBLEM_STATISTICS_AVAILABLE=True)
class TestProblemStatistics(TestCase):
    fixtures = ['test_users', 'test_full_package',
                'test_contest', 'test_problem_instance',
                'test_extra_contests', 'test_extra_problem_instance',
                'test_submissions_for_statistics',
                'test_extra_submissions_for_statistics']

    def test_statistics_updating(self):
        Submission.objects \
                .select_for_update() \
                .filter(id__gt=4) \
                .update(kind='IGNORED')
        problem = Problem.objects.get(id=1)
        ps = problem.statistics
        self.assertTrue(ps.submitted == 0)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 0)

        # Count submissions for single user in single problem instance
        # compilation error
        update_problem_statistics({'submission_id': 1})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 0)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 0)

        # 0 pts
        update_problem_statistics({'submission_id': 2})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 0)

        # 42 pts
        update_problem_statistics({'submission_id': 3})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 42)

        # 100 pts
        update_problem_statistics({'submission_id': 4})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 1)
        self.assertTrue(ps.avg_best_score == 100)

        # ignore 100 pts
        submission = Submission.objects.select_for_update().get(id=4)
        submission.kind = 'IGNORED'
        submission.save()
        submission.problem_instance.problem.controller \
                .recalculate_statistics_for_user(submission.user)
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 42)

        # unignore 100 pts
        submission = Submission.objects.select_for_update().get(id=4)
        submission.kind = 'NORMAL'
        submission.save()
        submission.problem_instance.problem.controller \
                .recalculate_statistics_for_user(submission.user)
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 1)
        self.assertTrue(ps.avg_best_score == 100)

        # delete 100 pts
        submission = Submission.objects.select_for_update().get(id=4).delete()
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 42)

    def test_statistics_probleminstances(self):
        Submission.objects \
                .select_for_update() \
                .filter(id__gt=8) \
                .update(kind='IGNORED')

        problem = Problem.objects.get(id=1)
        ps = problem.statistics
        self.assertTrue(ps.submitted == 0)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 0)

        # Count submissions for two users in two problem instances
        # user1 to pinstance1 100 pts
        update_problem_statistics({'submission_id': 4})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 1)
        self.assertTrue(ps.avg_best_score == 100)

        # user1 to pinstance2 100 pts
        update_problem_statistics({'submission_id': 5})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 1)
        self.assertTrue(ps.solved == 1)
        self.assertTrue(ps.avg_best_score == 100)

        # user2 to pinstance1 0 pts
        update_problem_statistics({'submission_id': 6})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 2)
        self.assertTrue(ps.solved == 1)
        self.assertTrue(ps.avg_best_score == 50)

        # user2 to pinstance2 50 pts
        update_problem_statistics({'submission_id': 7})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 2)
        self.assertTrue(ps.solved == 1)
        self.assertTrue(ps.avg_best_score == 75)

        # user2 to pinstance1 100 pts
        update_problem_statistics({'submission_id': 8})
        ps.refresh_from_db()
        self.assertTrue(ps.submitted == 2)
        self.assertTrue(ps.solved == 2)
        self.assertTrue(ps.avg_best_score == 100)

    def test_recalculate_statistics(self):
        problem = Problem.objects.get(id=1)
        ps = problem.statistics
        self.assertTrue(ps.submitted == 0)
        self.assertTrue(ps.solved == 0)
        self.assertTrue(ps.avg_best_score == 0)

        # Best scores for user1: 100, user2: 100, user3: 0, user4: None (CE)
        manager = recalculate_statistics.Command()
        manager.run_from_argv(['manage.py', 'recalculate_statistics'])

        # refresh_from_db() won't work because statistics were deleted
        problem = Problem.objects.get(id=1)
        ps = problem.statistics
        self.assertTrue(ps.submitted == 3)
        self.assertTrue(ps.solved == 2)
        self.assertTrue(ps.avg_best_score == 66)
