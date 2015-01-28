# -*- coding: utf-8 -*-
#
# Copyright (C) 2012 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.com/license.html.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/.

import shutil

from trac.util import create_unique_file
from trac.util.text import exception_to_unicode


_svn_components = [
    'svn_fs.SubversionConnector',
    'svn_prop.SubversionMergePropertyDiffRenderer',
    'svn_prop.SubversionMergePropertyRenderer',
    'svn_prop.SubversionPropertyRenderer',
]
_old_path = 'trac.versioncontrol.'
_new_path = 'tracopt.versioncontrol.svn.'


def do_upgrade(env, version, cursor):
    """Automatically enable tracopt.versioncontrol.svn.* components,
    unless they were explicitly disabled or the new svn components are
    already enabled.
    """
    enable = [c for c in _svn_components
              if env.is_component_enabled(_old_path + c) and
              not env.is_component_enabled(_new_path + c)]
    if not enable:
        return
    try:
        backup, f = create_unique_file(env.config.filename
                                       + '.tracopt-svn.bak')
        f.close()
        shutil.copyfile(env.config.filename, backup)
        env.log.info("Saved backup of configuration file in %s", backup)
    except IOError, e:
        env.log.warn("Couldn't save backup of configuration file (%s)",
                     exception_to_unicode(e))
    for c in enable:
        env.config.set('components', _new_path + c, 'enabled')
    env.config.save()
    env.log.info("Enabled components %r to cope with the move from %s to %s.",
                 enable,
                 _old_path.replace('.', '/'), _new_path.replace('.', '/'))
