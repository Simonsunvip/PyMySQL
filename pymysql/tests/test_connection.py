import datetime
import decimal
import pymysql
import time
import os
import copy
from pymysql.tests import base


class TestConnection(base.PyMySQLTestCase):
    def test_utf8mb4(self):
        """This test requires MySQL >= 5.5"""
        arg = self.databases[0].copy()
        arg['charset'] = 'utf8mb4'
        conn = pymysql.connect(**arg)

    def test_largedata(self):
        """Large query and response (>=16MB)"""
        cur = self.connections[0].cursor()
        cur.execute("SELECT @@max_allowed_packet")
        if cur.fetchone()[0] < 16*1024*1024 + 10:
            print("Set max_allowed_packet to bigger than 17MB")
            return
        t = 'a' * (16*1024*1024)
        cur.execute("SELECT '" + t + "'")
        assert cur.fetchone()[0] == t

    def test_autocommit(self):
        con = self.connections[0]
        self.assertFalse(con.get_autocommit())

        cur = con.cursor()
        cur.execute("SET AUTOCOMMIT=1")
        self.assertTrue(con.get_autocommit())

        con.autocommit(False)
        self.assertFalse(con.get_autocommit())
        cur.execute("SELECT @@AUTOCOMMIT")
        self.assertEqual(cur.fetchone()[0], 0)

    def test_select_db(self):
        con = self.connections[0]
        current_db = self.databases[0]['db']
        other_db = self.databases[1]['db']

        cur = con.cursor()
        cur.execute('SELECT database()')
        self.assertEqual(cur.fetchone()[0], current_db)

        con.select_db(other_db)
        cur.execute('SELECT database()')
        self.assertEqual(cur.fetchone()[0], other_db)

    def test_connection_gone_away(self):
        """
        http://dev.mysql.com/doc/refman/5.0/en/gone-away.html
        http://dev.mysql.com/doc/refman/5.0/en/error-messages-client.html#error_cr_server_gone_error
        """
        con = self.connections[0]
        cur = con.cursor()
        cur.execute("SET wait_timeout=1")
        time.sleep(2)
        with self.assertRaises(pymysql.OperationalError) as cm:
            cur.execute("SELECT 1+1")
        # error occures while reading, not writing because of socket buffer.
        #self.assertEquals(cm.exception.args[0], 2006)
        self.assertIn(cm.exception.args[0], (2006, 2013))

    def test_init_command(self):
        conn = pymysql.connect(
            init_command='SELECT "bar"; SELECT "baz"',
            **self.databases[0]
        )
        c = conn.cursor()
        c.execute('select "foobar";')
        self.assertEqual(('foobar',), c.fetchone())
        conn.close()

    def test_plugin(self):
        con = self.connections[0]
        self.assertEqual('mysql_native_password',con.get_plugin_name())

        # attempt a unix socket test which is included in some versions
        # and doesn't require a client side handler
        user = os.environ.get('USER')
        if not user or self.databases[0]['host'] != 'localhost':
            return
        cur = con.cursor()
        cur.execute("SHOW PLUGINS")
        socketfound = False
        socket_added = False
        two = three = False
        pam_plugin = False
        for r in cur:
            if (r[1], r[2], r[3]) ==  (u'ACTIVE', u'AUTHENTICATION', u'auth_socket.so'):
                plugin_name = r[0]
                socketfound = True
            if (r[1], r[2], r[3]) ==  (u'ACTIVE', u'AUTHENTICATION', u'dialog_examples.so'):
                if r[0] == 'two_questions':
                    two=True
                elif r[0] == 'three_attempts':
                    three=True
                socketfound = True
            if (r[0], r[1], r[2]) ==  (u'pam', u'ACTIVE', u'AUTHENTICATION'):
                pam_plugin = r[3].split('.')[0]
                if pam_plugin == 'auth_pam':
                    pam_plugin = 'pam'
                # MySQL: authentication_pam
                # https://dev.mysql.com/doc/refman/5.5/en/pam-authentication-plugin.html

                # MariaDB: pam
                # https://mariadb.com/kb/en/mariadb/pam-authentication-plugin/
                # uses client plugin 'dialog' by default however 'mysql_cleartext_password' if
                # variable pam-use-cleartext-plugin enabled
        if not socketfound:
            # needs plugin. lets install it.
            try:
                cur.execute("install plugin auth_socket soname 'auth_socket.so'")
                socket_plugin_name = 'auth_socket'
                socket_added = True
            except pymysql.err.InternalError:
                cur.execute("install soname 'auth_socket'")
                socket_plugin_name = 'unix_socket'
                socket_added = True

        current_db = self.databases[0]['db']
        db = copy.copy(self.databases[0])
        del db['user']
        if socketfound or socket_added:
            cur.execute("CREATE USER %s@localhost IDENTIFIED WITH %s" % ( user, plugin_name))
            cur.execute("GRANT ALL ON %s TO %s@localhost" % ( current_db, user))
            c = pymysql.connect(user=user, **db)
            if socket_added:
                cur.execute("uninstall soname 'auth_socket'")
            cur.execute("DROP USER %s@localhost" % user)

        class Dialog(object):
            m = {'Password, please:': b'notverysecret',
                 'Are you sure ?': b'yes, of course'}
            fail=False

            def __init__(self, con):
                self.con=con

            def prompt(self, echo, prompt):
                if self.fail:
                   self.fail=False
                   return 'bad guess'
                return self.m.get(prompt)

        if two:
            cur.execute("CREATE USER pymysql_test_two_questions" \
                        " IDENTIFIED WITH two_questions" \
                        " AS 'notverysecret'")
            cur.execute("GRANT ALL ON %s TO pymysql_test_two_questions" % current_db)
            c = pymysql.connect(user='pymysql_test_two_questions', plugin_map={b'dialog': Dialog}, **db)
            cur.execute("DROP USER pymysql_test_two_questions")

        if three:
            Dialog.m = {'Password, please:': b'stillnotverysecret'}
            Dialog.fail=True   # fail just once. We've got three attempts after all
            cur.execute("CREATE USER pymysql_test_three_attempts"
                        " IDENTIFIED WITH three_attempts" \
                        " AS 'stillnotverysecret'")
            cur.execute("GRANT ALL ON %s TO pymysql_test_three_attempts" % current_db)
            c = pymysql.connect(user='pymysql_test_three_attempts', plugin_map={b'dialog': Dialog}, **db)
            cur.execute("DROP USER pymysql_test_three_attempts")

        if pam_plugin:
            cur.execute("CREATE USER %s IDENTIFIED WITH %s" % ( user, pam_plugin))
            cur.execute("GRANT ALL ON %s TO %s" % ( current_db, user))
            c = pymysql.connect(user=user, **db)
            cur.execute("DROP USER %s" % user)


# A custom type and function to escape it
class Foo(object):
    value = "bar"


def escape_foo(x, d):
    return x.value


class TestEscape(base.PyMySQLTestCase):
    def test_escape_string(self):
        con = self.connections[0]
        cur = con.cursor()

        self.assertEqual(con.escape("foo'bar"), "'foo\\'bar'")
        cur.execute("SET sql_mode='NO_BACKSLASH_ESCAPES'")
        self.assertEqual(con.escape("foo'bar"), "'foo''bar'")

    def test_escape_builtin_encoders(self):
        con = self.connections[0]
        cur = con.cursor()

        val = datetime.datetime(2012, 3, 4, 5, 6)
        self.assertEqual(con.escape(val, con.encoders), "'2012-03-04 05:06:00'")

    def test_escape_custom_object(self):
        con = self.connections[0]
        cur = con.cursor()

        mapping = {Foo: escape_foo}
        self.assertEqual(con.escape(Foo(), mapping), "bar")

    def test_escape_fallback_encoder(self):
        con = self.connections[0]
        cur = con.cursor()

        class Custom(str):
            pass

        mapping = {pymysql.text_type: pymysql.escape_string}
        self.assertEqual(con.escape(Custom('foobar'), mapping), "'foobar'")

    def test_escape_no_default(self):
        con = self.connections[0]
        cur = con.cursor()

        self.assertRaises(TypeError, con.escape, 42, {})

    def test_escape_dict_value(self):
        con = self.connections[0]
        cur = con.cursor()

        mapping = con.encoders.copy()
        mapping[Foo] = escape_foo
        self.assertEqual(con.escape({'foo': Foo()}, mapping), {'foo': "bar"})

    def test_escape_list_item(self):
        con = self.connections[0]
        cur = con.cursor()

        mapping = con.encoders.copy()
        mapping[Foo] = escape_foo
        self.assertEqual(con.escape([Foo()], mapping), "(bar)")
