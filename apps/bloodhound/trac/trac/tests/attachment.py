# -*- coding: utf-8 -*-

import os.path
import shutil
from StringIO import StringIO
import tempfile
import unittest

from trac.attachment import Attachment, AttachmentModule
from trac.core import Component, implements, TracError
from trac.perm import IPermissionPolicy, PermissionCache
from trac.resource import Resource, resource_exists
from trac.test import EnvironmentStub
from trac.tests.resource import TestResourceChangeListener


hashes = {
    '42': '92cfceb39d57d914ed8b14d0e37643de0797ae56',
    'Foo.Mp3': '95797b6eb253337ff2c54e0881e2b747ec394f51',
    'SomePage': 'd7e80bae461ca8568e794792f5520b603f540e06',
    'Teh bar.jpg': 'ed9102c4aa099e92baf1073f824d21c5e4be5944',
    'Teh foo.txt': 'ab97ba98d98fcf72b92e33a66b07077010171f70',
    'bar.7z': '6c9600ad4d59ac864e6f0d2030c1fc76b4b406cb',
    'bar.jpg': 'ae0faa593abf2b6f8871f6f32fe5b28d1c6572be',
    'foo.$$$': 'eefc6aa745dbe129e8067a4a57637883edd83a8a',
    'foo.2.txt': 'a8fcfcc2ef4e400ee09ae53c1aabd7f5a5fda0c7',
    'foo.txt': '9206ac42b532ef8e983470c251f4e1a365fd636c',
    u'bar.aäc': '70d0e3b813fdc756602d82748719a3ceb85cbf29',
    u'ÜberSicht': 'a16c6837f6d3d2cc3addd68976db1c55deb694c8',
}


class TicketOnlyViewsTicket(Component):
    implements(IPermissionPolicy)

    def check_permission(self, action, username, resource, perm):
        if action.startswith('TICKET_'):
            return resource.realm == 'ticket'
        else:
            return None


class AttachmentTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub()
        self.env.path = os.path.join(tempfile.gettempdir(), 'trac-tempenv')
        os.mkdir(self.env.path)
        self.attachments_dir = os.path.join(self.env.path, 'files',
                                            'attachments')
        self.env.config.set('trac', 'permission_policies',
                            'TicketOnlyViewsTicket, LegacyAttachmentPolicy')
        self.env.config.set('attachment', 'max_size', 512)

        self.perm = PermissionCache(self.env)

    def tearDown(self):
        shutil.rmtree(self.env.path)
        self.env.reset_db()

    def test_get_path(self):
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.filename = 'foo.txt'
        self.assertEqual(os.path.join(self.attachments_dir, 'ticket',
                                      hashes['42'][0:3], hashes['42'],
                                      hashes['foo.txt'] + '.txt'),
                         attachment.path)
        attachment = Attachment(self.env, 'wiki', 'SomePage')
        attachment.filename = 'bar.jpg'
        self.assertEqual(os.path.join(self.attachments_dir, 'wiki',
                                      hashes['SomePage'][0:3],
                                      hashes['SomePage'],
                                      hashes['bar.jpg'] + '.jpg'),
                         attachment.path)

    def test_path_extension(self):
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.filename = 'Foo.Mp3'
        self.assertEqual(os.path.join(self.attachments_dir, 'ticket',
                                      hashes['42'][0:3], hashes['42'],
                                      hashes['Foo.Mp3'] + '.Mp3'),
                         attachment.path)
        attachment = Attachment(self.env, 'wiki', 'SomePage')
        attachment.filename = 'bar.7z'
        self.assertEqual(os.path.join(self.attachments_dir, 'wiki',
                                      hashes['SomePage'][0:3],
                                      hashes['SomePage'],
                                      hashes['bar.7z'] + '.7z'),
                         attachment.path)
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.filename = 'foo.$$$'
        self.assertEqual(os.path.join(self.attachments_dir, 'ticket',
                                      hashes['42'][0:3], hashes['42'],
                                      hashes['foo.$$$']),
                         attachment.path)
        attachment = Attachment(self.env, 'wiki', 'SomePage')
        attachment.filename = u'bar.aäc'
        self.assertEqual(os.path.join(self.attachments_dir, 'wiki',
                                      hashes['SomePage'][0:3],
                                      hashes['SomePage'],
                                      hashes[u'bar.aäc']),
                         attachment.path)

    def test_get_path_encoded(self):
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.filename = 'Teh foo.txt'
        self.assertEqual(os.path.join(self.attachments_dir, 'ticket',
                                      hashes['42'][0:3], hashes['42'],
                                      hashes['Teh foo.txt'] + '.txt'),
                         attachment.path)
        attachment = Attachment(self.env, 'wiki', u'ÜberSicht')
        attachment.filename = 'Teh bar.jpg'
        self.assertEqual(os.path.join(self.attachments_dir, 'wiki',
                                      hashes[u'ÜberSicht'][0:3],
                                      hashes[u'ÜberSicht'],
                                      hashes['Teh bar.jpg'] + '.jpg'),
                         attachment.path)

    def test_select_empty(self):
        self.assertRaises(StopIteration,
                          Attachment.select(self.env, 'ticket', 42).next)
        self.assertRaises(StopIteration,
                          Attachment.select(self.env, 'wiki', 'SomePage').next)

    def test_insert(self):
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.insert('foo.txt', StringIO(''), 0, 1)
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.insert('bar.jpg', StringIO(''), 0, 2)

        attachments = Attachment.select(self.env, 'ticket', 42)
        self.assertEqual('foo.txt', attachments.next().filename)
        self.assertEqual('bar.jpg', attachments.next().filename)
        self.assertRaises(StopIteration, attachments.next)

    def test_insert_unique(self):
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.insert('foo.txt', StringIO(''), 0)
        self.assertEqual('foo.txt', attachment.filename)
        attachment = Attachment(self.env, 'ticket', 42)
        attachment.insert('foo.txt', StringIO(''), 0)
        self.assertEqual('foo.2.txt', attachment.filename)
        self.assertEqual(os.path.join(self.attachments_dir, 'ticket',
                                      hashes['42'][0:3], hashes['42'],
                                      hashes['foo.2.txt'] + '.txt'),
                         attachment.path)
        self.assert_(os.path.exists(attachment.path))

    def test_insert_outside_attachments_dir(self):
        attachment = Attachment(self.env, '../../../../../sth/private', 42)
        self.assertRaises(TracError, attachment.insert, 'foo.txt',
                          StringIO(''), 0)

    def test_delete(self):
        attachment1 = Attachment(self.env, 'wiki', 'SomePage')
        attachment1.insert('foo.txt', StringIO(''), 0)
        attachment2 = Attachment(self.env, 'wiki', 'SomePage')
        attachment2.insert('bar.jpg', StringIO(''), 0)

        attachments = Attachment.select(self.env, 'wiki', 'SomePage')
        self.assertEqual(2, len(list(attachments)))

        attachment1.delete()
        attachment2.delete()

        assert not os.path.exists(attachment1.path)
        assert not os.path.exists(attachment2.path)

        attachments = Attachment.select(self.env, 'wiki', 'SomePage')
        self.assertEqual(0, len(list(attachments)))

    def test_delete_file_gone(self):
        """
        Verify that deleting an attachment works even if the referenced file
        doesn't exist for some reason.
        """
        attachment = Attachment(self.env, 'wiki', 'SomePage')
        attachment.insert('foo.txt', StringIO(''), 0)
        os.unlink(attachment.path)

        attachment.delete()

    def test_reparent(self):
        attachment1 = Attachment(self.env, 'wiki', 'SomePage')
        attachment1.insert('foo.txt', StringIO(''), 0)
        path1 = attachment1.path
        attachment2 = Attachment(self.env, 'wiki', 'SomePage')
        attachment2.insert('bar.jpg', StringIO(''), 0)

        attachments = Attachment.select(self.env, 'wiki', 'SomePage')
        self.assertEqual(2, len(list(attachments)))
        attachments = Attachment.select(self.env, 'ticket', 123)
        self.assertEqual(0, len(list(attachments)))
        assert os.path.exists(path1) and os.path.exists(attachment2.path)

        attachment1.reparent('ticket', 123)
        self.assertEqual('ticket', attachment1.parent_realm)
        self.assertEqual('ticket', attachment1.resource.parent.realm)
        self.assertEqual('123', attachment1.parent_id)
        self.assertEqual('123', attachment1.resource.parent.id)

        attachments = Attachment.select(self.env, 'wiki', 'SomePage')
        self.assertEqual(1, len(list(attachments)))
        attachments = Attachment.select(self.env, 'ticket', 123)
        self.assertEqual(1, len(list(attachments)))
        assert not os.path.exists(path1) and os.path.exists(attachment1.path)
        assert os.path.exists(attachment2.path)

    def test_legacy_permission_on_parent(self):
        """Ensure that legacy action tests are done on parent.  As
        `ATTACHMENT_VIEW` maps to `TICKET_VIEW`, the `TICKET_VIEW` is tested
        against the ticket's resource."""
        attachment = Attachment(self.env, 'ticket', 42)
        self.assert_('ATTACHMENT_VIEW' in self.perm(attachment.resource))

    def test_resource_doesnt_exist(self):
        r = Resource('wiki', 'WikiStart').child('attachment', 'file.txt')
        self.assertEqual(False, AttachmentModule(self.env).resource_exists(r))

    def test_resource_exists(self):
        att = Attachment(self.env, 'wiki', 'WikiStart')
        att.insert('file.txt', StringIO(''), 1)
        self.assertTrue(resource_exists(self.env, att.resource))


class AttachmentResourceChangeListenerTestCase(unittest.TestCase):
    DUMMY_PARENT_REALM = "wiki"
    DUMMY_PARENT_ID = "WikiStart"

    def setUp(self):
        self.env = EnvironmentStub(default_data=True)
        self.listener = TestResourceChangeListener(self.env)
        self.listener.resource_type = Attachment
        self.listener.callback = self.listener_callback

    def tearDown(self):
        self.env.reset_db()

    def test_change_listener_created(self):
        attachment = self._create_attachment()
        self.assertEqual('created', self.listener.action)
        self.assertTrue(isinstance(self.listener.resource, Attachment))
        self.assertEqual(attachment.filename, self.filename)
        self.assertEqual(attachment.parent_realm, self.parent_realm)
        self.assertEqual(attachment.parent_id, self.parent_id)

    def test_change_listener_reparent(self):
        attachment = self._create_attachment()
        attachment.reparent(self.DUMMY_PARENT_REALM, "SomePage")

        self.assertEqual('changed', self.listener.action)
        self.assertTrue(isinstance(self.listener.resource, Attachment))
        self.assertEqual(attachment.filename, self.filename)
        self.assertEqual(attachment.parent_realm, self.parent_realm)
        self.assertEqual("SomePage", self.parent_id)
        self.assertNotIn("parent_realm", self.listener.old_values)
        self.assertEqual(
            self.DUMMY_PARENT_ID, self.listener.old_values["parent_id"])

    def test_change_listener_deleted(self):
        attachment = self._create_attachment()
        attachment.delete()
        self.assertEqual('deleted', self.listener.action)
        self.assertTrue(isinstance(self.listener.resource, Attachment))
        self.assertEqual(attachment.filename, self.filename)

    def _create_attachment(self):
        attachment = Attachment(
            self.env, self.DUMMY_PARENT_REALM, self.DUMMY_PARENT_ID)
        attachment.insert('file.txt', StringIO(''), 1)
        return attachment

    def listener_callback(self, action, resource, context, old_values = None):
        self.parent_realm = resource.parent_realm
        self.parent_id = resource.parent_id
        self.filename = resource.filename

def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(AttachmentTestCase, 'test'))
    suite.addTest(unittest.makeSuite(
        AttachmentResourceChangeListenerTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
