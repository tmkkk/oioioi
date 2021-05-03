class MossClient(object):
    HOSTNAME = 'moss.stanford.edu'
    PORT = 7690

    def __init__(self, userid):
        self.userid = userid