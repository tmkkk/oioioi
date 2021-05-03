import os
import re
import shutil
import six
import socket
import tempfile

OIOIOI_LANGUAGE_TO_MOSS = {
    "C++": "cc",
    "C": "c",
    "Python": "python",
    "Pascal": "pascal",
    "Java": "java",
}


class MossException(Exception):
    pass


# Based on: https://github.com/soachishti/moss.py
class MossClient(object):
    HOSTNAME = 'moss.stanford.edu'
    PORT = 7690
    RESULT_URL_REGEX = re.compile(r"^http://moss\.stanford\.edu/results/\d+/\d+$")

    def __init__(self, userid, lang):
        self.userid = userid
        self.lang = OIOIOI_LANGUAGE_TO_MOSS[lang]
        self.files = []

    def add_file(self, filepath, name):
        self.files.append((filepath, name))

    def submit(self, query_comment=""):
        sock = socket.socket()
        sock.connect((self.HOSTNAME, self.PORT))

        prelude = (
            "moss %(userid)d\n"
            "directory %(directory_mode)d\n"
            "X %(experimental)d\n"
            "maxmatches %(maxmatches)d\n"
            "show %(show)d\n"
            "language %(language)s\n"
            % {
                # default MOSS settings taken from the official script
                'userid': self.userid,
                'directory_mode': 0,
                'experimental': 0,
                'maxmatches': 10,
                'show': 250,
                'language': self.lang,
            }
        )
        sock.send(six.ensure_binary(prelude))
        response = six.ensure_text(sock.recv(32))
        if response.startswith("no"):
            sock.send(six.ensure_binary("end\n"))
            sock.close()
            raise MossException("Moss rejected the query")

        for i, (path, name) in enumerate(self.files):
            size = os.path.getsize(path)
            message = "file %d %s %d %s\n" % (
                i + 1,  # file id
                self.lang,  # programming language
                size,  # file size
                name,  # name of the submission
            )
            sock.send(six.ensure_binary(message))
            with open(path, "rb") as f:
                sock.send(f.read(size))

        sock.send(six.ensure_binary("query 0 %s\n" % query_comment))
        url = sock.recv(256)
        url = six.ensure_text(url).replace('\n', '')
        sock.send(six.ensure_binary("end\n"))
        sock.close()
        print("[%s]" % url)

        if not self.RESULT_URL_REGEX.match(url):
            raise MossException("Moss returned invalid url")

        return url


def submit_and_get_url(client, submission_collector):
    submission_list = submission_collector.collect_list()
    tmpdir = tempfile.mkdtemp()
    try:
        for s in submission_list:
            display_name = (
                (s.first_name[0] if s.first_name else '')
                + (s.last_name[0] if s.last_name else '')
                + six.text_type(s.user_id)
                + '_'
                + six.text_type(s.submission_id)
            )
            dest = os.path.join(tmpdir, display_name)
            submission_collector.get_submission_source(dest, s.source_file)
            client.add_file(dest, display_name)
        return client.submit()
    finally:
        shutil.rmtree(tmpdir)
