import util


class FileEndpointTest(util.WebTest):
    """This will try to access a file with and without authorization
    using the url path

    Registered users should have access to all files,
    not registered users should have no access at all
    """
    def test_registered(self):
        """Test as registered user, should be ok (200)"""
        f = self.new_file()
        url = self.app.media.get(f.data).content_url

        session = self.new_session()  # fake login

        self.api.get(url, token=session.token, status_code=200)

    def test_not_registered(self):
        """Test as unregistered user, credentials are missing, 401 expected"""
        f = self.new_file()
        url = self.app.media.get(f.data).content_url
        self.api.get(url, status_code=401)
