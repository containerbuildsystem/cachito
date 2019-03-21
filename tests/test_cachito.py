import unittest

import cachito


class CachitoTestCase(unittest.TestCase):

    def setUp(self):
        self.app = cachito.app.test_client()

    def test_index(self):
        rv = self.app.get('/')
        self.assertIn('Welcome to cachito', rv.data.decode())


if __name__ == '__main__':
    unittest.main()
