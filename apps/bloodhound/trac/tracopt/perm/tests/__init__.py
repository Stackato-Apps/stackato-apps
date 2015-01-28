# -*- coding: utf-8 -*-
#
# Copyright (C) 2012 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.org/wiki/TracLicense.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/log/.

import unittest

from tracopt.perm.tests import authz_policy


def suite():
    suite = unittest.TestSuite()
    suite.addTest(authz_policy.suite())
    return suite


if __name__ == '__main__':
    unittest.main(defaultTest='suite')
