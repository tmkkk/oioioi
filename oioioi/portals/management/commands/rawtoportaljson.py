import json
import os
import string

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext_lazy as _


class Command(BaseCommand):
    help = _(
        "Creates json describing portal structure from list of all "
        "tasks from MAIN portal (default one in "
        "oioioi/portals/management/commands/res) you may then edit "
        "to add contest descriptions and all of the other necessary "
        "polish."
    ).encode('utf-8')

    def add_arguments(self, parser):
        parser.add_argument(
            'out_filename_json', type=str, help='File json will be written to'
        )
        parser.add_argument(
            'in_filename_txt',
            type=str,
            nargs='?',
            default=os.path.join(os.path.dirname(__file__), 'data.txt'),
            help='Input file',
        )

    def handle(self, *args, **options):
        try:
            in_data = open(options['in_filename_txt'], "r").read()
            out_file = open(options['out_filename_json'], "w")
        except IOError as e:
            raise CommandError(e.message)

        try:
            p = [
                json.loads(e)['tags']
                for e in in_data.replace('\'', '\"').split('\n')
                if e
            ]
        except ValueError:
            raise CommandError(_("Some lines of the input are not JSON"))

        tree_d = {}

        # creating the tree
        for name, contest in p:
            i = 0
            while contest[i] not in string.digits:
                i += 1
            family, edition = contest[:i], contest[i:]

            if family not in tree_d:
                tree_d[family] = {
                    'name': family.lower(),
                    'long_name': family,
                    'description': "placeholder",
                    'editions': {},
                }
            family = tree_d[family]

            if edition not in family['editions']:
                family['editions'][edition] = {
                    'name': edition,
                    'long_name': family['long_name'] + " " + edition,
                    'tag': contest,
                    'tasks': [
                        ("placeholder", []),
                    ],
                }
            edition = family['editions'][edition]

            # non-usual structure of 'tasks' field is caused by feature
            # that is only used by another generator; it's list of tuples
            # containing pairs of header and task list
            if name not in edition['tasks'][0][1]:
                edition['tasks'][0][1].append(name)

        # sorting edition numbers as strings (but some of them are not numbers,
        # see 'PA2002-2'), it's tricky
        for name in tree_d:
            tree_d[name]['editions'] = [
                e[1]
                for e in sorted(
                    list(tree_d[name]['editions'].items()),
                    cmp=lambda a, b: -cmp(int(a[0]), int(b[0]))
                    if (a[0].isdigit() and b[0].isdigit())
                    else -cmp(a[0], b[0]),
                )
            ]

        tree_d = [e[1] for e in sorted(tree_d.items())]

        json.dump(tree_d, out_file, indent=4, separators=(',', ': '))
        out_file.close()
