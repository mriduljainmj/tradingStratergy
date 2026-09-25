import os
import subprocess
import sys
import unittest


class DatabaseUrlTests(unittest.TestCase):
    def test_postgres_urls_use_installed_psycopg2_driver(self):
        for url in (
            'postgresql://user:password@localhost:5432/app',
            'postgresql+psycopg://user:password@localhost:5432/app',
        ):
            with self.subTest(url=url):
                env = os.environ.copy()
                env['DATABASE_URL'] = url
                result = subprocess.run(
                    [sys.executable, '-c', 'from db.database import engine; print(engine.url.drivername)'],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.stdout.strip(), 'postgresql+psycopg2')
