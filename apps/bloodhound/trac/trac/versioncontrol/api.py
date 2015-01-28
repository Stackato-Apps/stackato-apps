# -*- coding: utf-8 -*-
#
# Copyright (C)2005-2011 Edgewall Software
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.org/wiki/TracLicense.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/log/.
#
# Author: Christopher Lenz <cmlenz@gmx.de>

from __future__ import with_statement

import os.path
import time

from trac.admin import AdminCommandError, IAdminCommandProvider, get_dir_list
from trac.config import ConfigSection, ListOption, Option
from trac.core import *
from trac.resource import IResourceManager, Resource, ResourceNotFound
from trac.util.concurrency import threading
from trac.util.text import printout, to_unicode
from trac.util.translation import _
from trac.web.api import IRequestFilter


def is_default(reponame):
    """Check whether `reponame` is the default repository."""
    return not reponame or reponame in ('(default)', _('(default)'))


class IRepositoryConnector(Interface):
    """Provide support for a specific version control system."""

    error = None # place holder for storing relevant error message

    def get_supported_types():
        """Return the types of version control systems that are supported.

        Yields `(repotype, priority)` pairs, where `repotype` is used to
        match against the configured `[trac] repository_type` value in TracIni.

        If multiple provider match a given type, the `priority` is used to
        choose between them (highest number is highest priority).

        If the `priority` returned is negative, this indicates that the
        connector for the given `repotype` indeed exists but can't be
        used for some reason. The `error` property can then be used to
        store an error message or exception relevant to the problem detected.
        """

    def get_repository(repos_type, repos_dir, params):
        """Return a Repository instance for the given repository type and dir.
        """


class IRepositoryProvider(Interface):
    """Provide known named instances of Repository."""

    def get_repositories():
        """Generate repository information for known repositories.

        Repository information is a key,value pair, where the value is
        a dictionary which must contain at the very least either of
        the following entries:

         - `'dir'`: the repository directory which can be used by the
                    connector to create a `Repository` instance. This
                    defines a "real" repository.

         - `'alias'`: the name of another repository. This defines an
                      alias to another (real) repository.

        Optional entries:

         - `'type'`: the type of the repository (if not given, the
                     default repository type will be used).

         - `'description'`: a description of the repository (can
                            contain WikiFormatting).

         - `'hidden'`: if set to `'true'`, the repository is hidden
                       from the repository index.

         - `'url'`: the base URL for checking out the repository.
        """


class IRepositoryChangeListener(Interface):
    """Listen for changes in repositories."""

    def changeset_added(repos, changeset):
        """Called after a changeset has been added to a repository."""

    def changeset_modified(repos, changeset, old_changeset):
        """Called after a changeset has been modified in a repository.

        The `old_changeset` argument contains the metadata of the changeset
        prior to the modification. It is `None` if the old metadata cannot
        be retrieved.
        """


class DbRepositoryProvider(Component):
    """Component providing repositories registered in the DB."""

    implements(IRepositoryProvider, IAdminCommandProvider)

    repository_attrs = ('alias', 'description', 'dir', 'hidden', 'name',
                        'type', 'url')

    # IRepositoryProvider methods

    def get_repositories(self):
        """Retrieve repositories specified in the repository DB table."""
        repos = {}
        for id, name, value in self.env.db_query(
                "SELECT id, name, value FROM repository WHERE name IN (%s)"
                % ",".join("'%s'" % each for each in self.repository_attrs)):
            if value is not None:
                repos.setdefault(id, {})[name] = value
        reponames = {}
        for id, info in repos.iteritems():
            if 'name' in info and ('dir' in info or 'alias' in info):
                info['id'] = id
                reponames[info['name']] = info
        return reponames.iteritems()

    # IAdminCommandProvider methods

    def get_admin_commands(self):
        yield ('repository add', '<repos> <dir> [type]',
               "Add a source repository",
               self._complete_add, self._do_add)
        yield ('repository alias', '<name> <target>',
               "Create an alias for a repository",
               self._complete_alias, self._do_alias)
        yield ('repository remove', '<repos>',
               "Remove a source repository",
               self._complete_repos, self._do_remove)
        yield ('repository set', '<repos> <key> <value>',
               """Set an attribute of a repository

               The following keys are supported: %s
               """ % ', '.join(self.repository_attrs),
               self._complete_set, self._do_set)

    def get_reponames(self):
        rm = RepositoryManager(self.env)
        return [reponame or '(default)' for reponame
                in rm.get_all_repositories()]

    def _complete_add(self, args):
        if len(args) == 2:
            return get_dir_list(args[-1], True)
        elif len(args) == 3:
            return RepositoryManager(self.env).get_supported_types()

    def _complete_alias(self, args):
        if len(args) == 2:
            return self.get_reponames()

    def _complete_repos(self, args):
        if len(args) == 1:
            return self.get_reponames()

    def _complete_set(self, args):
        if len(args) == 1:
            return self.get_reponames()
        elif len(args) == 2:
            return self.repository_attrs

    def _do_add(self, reponame, dir, type_=None):
        self.add_repository(reponame, os.path.abspath(dir), type_)

    def _do_alias(self, reponame, target):
        self.add_alias(reponame, target)

    def _do_remove(self, reponame):
        self.remove_repository(reponame)

    def _do_set(self, reponame, key, value):
        if key not in self.repository_attrs:
            raise AdminCommandError(_('Invalid key "%(key)s"', key=key))
        if key == 'dir':
            value = os.path.abspath(value)
        self.modify_repository(reponame, {key: value})
        if not reponame:
            reponame = '(default)'
        if key == 'dir':
            printout(_('You should now run "repository resync %(name)s".',
                       name=reponame))
        elif key == 'type':
            printout(_('You may have to run "repository resync %(name)s".',
                       name=reponame))

    # Public interface

    def add_repository(self, reponame, dir, type_=None):
        """Add a repository."""
        if not os.path.isabs(dir):
            raise TracError(_("The repository directory must be absolute"))
        if is_default(reponame):
            reponame = ''
        rm = RepositoryManager(self.env)
        if type_ and type_ not in rm.get_supported_types():
            raise TracError(_("The repository type '%(type)s' is not "
                              "supported", type=type_))
        with self.env.db_transaction as db:
            id = rm.get_repository_id(reponame)
            db.executemany(
                "INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
                [(id, 'dir', dir),
                 (id, 'type', type_ or '')])
        rm.reload_repositories()

    def add_alias(self, reponame, target):
        """Create an alias repository."""
        if is_default(reponame):
            reponame = ''
        if is_default(target):
            target = ''
        rm = RepositoryManager(self.env)
        with self.env.db_transaction as db:
            id = rm.get_repository_id(reponame)
            db.executemany(
                "INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
                [(id, 'dir', None),
                 (id, 'alias', target)])
        rm.reload_repositories()

    def remove_repository(self, reponame):
        """Remove a repository."""
        if is_default(reponame):
            reponame = ''
        rm = RepositoryManager(self.env)
        with self.env.db_transaction as db:
            id = rm.get_repository_id(reponame)
            db("DELETE FROM repository WHERE id=%s", (id,))
            db("DELETE FROM revision WHERE repos=%s", (id,))
            db("DELETE FROM node_change WHERE repos=%s", (id,))
        rm.reload_repositories()

    def modify_repository(self, reponame, changes):
        """Modify attributes of a repository."""
        if is_default(reponame):
            reponame = ''
        rm = RepositoryManager(self.env)
        with self.env.db_transaction as db:
            id = rm.get_repository_id(reponame)
            for (k, v) in changes.iteritems():
                if k not in self.repository_attrs:
                    continue
                if k in ('alias', 'name') and is_default(v):
                    v = ''
                if k == 'dir' and not os.path.isabs(v):
                    raise TracError(_("The repository directory must be "
                                      "absolute"))
                db("UPDATE repository SET value=%s WHERE id=%s AND name=%s",
                   (v, id, k))
                if not db(
                        "SELECT value FROM repository WHERE id=%s AND name=%s",
                        (id, k)):
                    db("""INSERT INTO repository (id, name, value)
                          VALUES (%s, %s, %s)
                          """, (id, k, v))
        rm.reload_repositories()


class RepositoryManager(Component):
    """Version control system manager."""

    implements(IRequestFilter, IResourceManager, IRepositoryProvider)

    connectors = ExtensionPoint(IRepositoryConnector)
    providers = ExtensionPoint(IRepositoryProvider)
    change_listeners = ExtensionPoint(IRepositoryChangeListener)

    repositories_section = ConfigSection('repositories',
        """One of the alternatives for registering new repositories is to
        populate the `[repositories]` section of the `trac.ini`.

        This is especially suited for setting up convenience aliases,
        short-lived repositories, or during the initial phases of an
        installation.

        See [TracRepositoryAdmin#Intrac.ini TracRepositoryAdmin] for details
        about the format adopted for this section and the rest of that page for
        the other alternatives.

        (''since 0.12'')""")

    repository_type = Option('trac', 'repository_type', 'svn',
        """Default repository connector type. (''since 0.10'')

        This is also used as the default repository type for repositories
        defined in [[TracIni#repositories-section repositories]] or using the
        "Repositories" admin panel. (''since 0.12'')
        """)

    repository_dir = Option('trac', 'repository_dir', '',
        """Path to the default repository. This can also be a relative path
        (''since 0.11'').

        This option is deprecated, and repositories should be defined in the
        [TracIni#repositories-section repositories] section, or using the
        "Repositories" admin panel. (''since 0.12'')""")

    repository_sync_per_request = ListOption('trac',
        'repository_sync_per_request', '(default)',
        doc="""List of repositories that should be synchronized on every page
        request.

        Leave this option empty if you have set up post-commit hooks calling
        `trac-admin $ENV changeset added` on all your repositories
        (recommended). Otherwise, set it to a comma-separated list of
        repository names. Note that this will negatively affect performance,
        and will prevent changeset listeners from receiving events from the
        repositories specified here. The default is to synchronize the default
        repository, for backward compatibility. (''since 0.12'')""")

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self._connectors = None
        self._all_repositories = None

    # IRequestFilter methods

    def pre_process_request(self, req, handler):
        from trac.web.chrome import Chrome, add_warning
        if handler is not Chrome(self.env):
            for reponame in self.repository_sync_per_request:
                start = time.time()
                if is_default(reponame):
                    reponame = ''
                try:
                    repo = self.get_repository(reponame)
                    if repo:
                        repo.sync()
                    else:
                        self.log.warning("Unable to find repository '%s' for "
                                         "synchronization",
                                         reponame or '(default)')
                        continue
                except TracError, e:
                    add_warning(req,
                        _("Can't synchronize with repository \"%(name)s\" "
                          "(%(error)s). Look in the Trac log for more "
                          "information.", name=reponame or '(default)',
                          error=to_unicode(e.message)))
                self.log.info("Synchronized '%s' repository in %0.2f seconds",
                              reponame or '(default)', time.time() - start)
        return handler

    def post_process_request(self, req, template, data, content_type):
        return (template, data, content_type)

    # IResourceManager methods

    def get_resource_realms(self):
        yield 'changeset'
        yield 'source'
        yield 'repository'

    def get_resource_description(self, resource, format=None, **kwargs):
        if resource.realm == 'changeset':
            parent = resource.parent
            reponame = parent and parent.id
            id = resource.id
            if reponame:
                return _("Changeset %(rev)s in %(repo)s", rev=id, repo=reponame)
            else:
                return _("Changeset %(rev)s", rev=id)
        elif resource.realm == 'source':
            parent = resource.parent
            reponame = parent and parent.id
            id = resource.id
            version = ''
            if format == 'summary':
                repos = self.get_repository(reponame)
                node = repos.get_node(resource.id, resource.version)
                if node.isdir:
                    kind = _("directory")
                elif node.isfile:
                    kind = _("file")
                if resource.version:
                    version = _(" at version %(rev)s", rev=resource.version)
            else:
                kind = _("path")
                if resource.version:
                    version = '@%s' % resource.version
            in_repo = _(" in %(repo)s", repo=reponame) if reponame else ''
            # TRANSLATOR: file /path/to/file.py at version 13 in reponame
            return _('%(kind)s %(id)s%(at_version)s%(in_repo)s',
                     kind=kind, id=id, at_version=version, in_repo=in_repo)
        elif resource.realm == 'repository':
            return _("Repository %(repo)s", repo=resource.id)

    def get_resource_url(self, resource, href, **kwargs):
        if resource.realm == 'changeset':
            parent = resource.parent
            return href.changeset(resource.id, parent and parent.id or None)
        elif resource.realm == 'source':
            parent = resource.parent
            return href.browser(parent and parent.id or None, resource.id,
                                rev=resource.version or None)
        elif resource.realm == 'repository':
            return href.browser(resource.id or None)

    def resource_exists(self, resource):
        if resource.realm == 'repository':
            reponame = resource.id
        else:
            reponame = resource.parent.id
        repos = self.env.get_repository(reponame)
        if not repos:
            return False
        if resource.realm == 'changeset':
            try:
                repos.get_changeset(resource.id)
                return True
            except NoSuchChangeset:
                return False
        elif resource.realm == 'source':
            try:
                repos.get_node(resource.id, resource.version)
                return True
            except NoSuchNode:
                return False
        elif resource.realm == 'repository':
            return True

    # IRepositoryProvider methods

    def get_repositories(self):
        """Retrieve repositories specified in TracIni.

        The `[repositories]` section can be used to specify a list
        of repositories.
        """
        repositories = self.repositories_section
        reponames = {}
        # eventually add pre-0.12 default repository
        if self.repository_dir:
            reponames[''] = {'dir': self.repository_dir}
        # first pass to gather the <name>.dir entries
        for option in repositories:
            if option.endswith('.dir'):
                reponames[option[:-4]] = {}
        # second pass to gather aliases
        for option in repositories:
            alias = repositories.get(option)
            if '.' not in option:   # Support <alias> = <repo> syntax
                option += '.alias'
            if option.endswith('.alias') and alias in reponames:
                reponames.setdefault(option[:-6], {})['alias'] = alias
        # third pass to gather the <name>.<detail> entries
        for option in repositories:
            if '.' in option:
                name, detail = option.rsplit('.', 1)
                if name in reponames and detail != 'alias':
                    reponames[name][detail] = repositories.get(option)

        for reponame, info in reponames.iteritems():
            yield (reponame, info)

    # Public API methods

    def get_supported_types(self):
        """Return the list of supported repository types."""
        types = set(type_ for connector in self.connectors
                    for (type_, prio) in connector.get_supported_types() or []
                    if prio >= 0)
        return list(types)

    def get_repositories_by_dir(self, directory):
        """Retrieve the repositories based on the given directory.

           :param directory: the key for identifying the repositories.
           :return: list of `Repository` instances.
        """
        directory = os.path.join(os.path.normcase(directory), '')
        repositories = []
        for reponame, repoinfo in self.get_all_repositories().iteritems():
            dir = repoinfo.get('dir')
            if dir:
                dir = os.path.join(os.path.normcase(dir), '')
                if dir.startswith(directory):
                    repos = self.get_repository(reponame)
                    if repos:
                        repositories.append(repos)
        return repositories

    def get_repository_id(self, reponame):
        """Return a unique id for the given repository name.

        This will create and save a new id if none is found.

        \note: this should probably be renamed as we're dealing
               exclusively with *db* repository ids here.
        """
        with self.env.db_transaction as db:
            for id, in db(
                    "SELECT id FROM repository WHERE name='name' AND value=%s",
                    (reponame,)):
                return id

            id = db("SELECT COALESCE(MAX(id), 0) FROM repository")[0][0] + 1
            db("INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
               (id, 'name', reponame))
            return id

    def get_repository(self, reponame):
        """Retrieve the appropriate `Repository` for the given
        repository name.

           :param reponame: the key for specifying the repository.
                            If no name is given, take the default
                            repository.
           :return: if no corresponding repository was defined,
                    simply return `None`.
        """
        reponame = reponame or ''
        repoinfo = self.get_all_repositories().get(reponame, {})
        if 'alias' in repoinfo:
            reponame = repoinfo['alias']
            repoinfo = self.get_all_repositories().get(reponame, {})
        rdir = repoinfo.get('dir')
        if not rdir:
            return None
        rtype = repoinfo.get('type') or self.repository_type

        # get a Repository for the reponame (use a thread-level cache)
        with self.env.db_transaction: # prevent possible deadlock, see #4465
            with self._lock:
                tid = threading._get_ident()
                if tid in self._cache:
                    repositories = self._cache[tid]
                else:
                    repositories = self._cache[tid] = {}
                repos = repositories.get(reponame)
                if not repos:
                    if not os.path.isabs(rdir):
                        rdir = os.path.join(self.env.path, rdir)
                    connector = self._get_connector(rtype)
                    repos = connector.get_repository(rtype, rdir,
                                                     repoinfo.copy())
                    repositories[reponame] = repos
                return repos

    def get_repository_by_path(self, path):
        """Retrieve a matching `Repository` for the given `path`.

        :param path: the eventually scoped repository-scoped path
        :return: a `(reponame, repos, path)` triple, where `path` is
                 the remaining part of `path` once the `reponame` has
                 been truncated, if needed.
        """
        matches = []
        path = path.strip('/') + '/' if path else '/'
        for reponame in self.get_all_repositories().keys():
            stripped_reponame = reponame.strip('/') + '/'
            if path.startswith(stripped_reponame):
                matches.append((len(stripped_reponame), reponame))
        if matches:
            matches.sort()
            length, reponame = matches[-1]
            path = path[length:]
        else:
            reponame = ''
        return (reponame, self.get_repository(reponame),
                path.rstrip('/') or '/')

    def get_default_repository(self, context):
        """Recover the appropriate repository from the current context.

        Lookup the closest source or changeset resource in the context
        hierarchy and return the name of its associated repository.
        """
        while context:
            if context.resource.realm in ('source', 'changeset'):
                return context.resource.parent.id
            context = context.parent

    def get_all_repositories(self):
        """Return a dictionary of repository information, indexed by name."""
        if not self._all_repositories:
            all_repositories = {}
            for provider in self.providers:
                for reponame, info in provider.get_repositories() or []:
                    if reponame in all_repositories:
                        self.log.warn("Discarding duplicate repository '%s'",
                                      reponame)
                    else:
                        info['name'] = reponame
                        if 'id' not in info:
                            info['id'] = self.get_repository_id(reponame)
                        all_repositories[reponame] = info
            self._all_repositories = all_repositories
        return self._all_repositories

    def get_real_repositories(self):
        """Return a set of all real repositories (i.e. excluding aliases)."""
        repositories = set()
        for reponame in self.get_all_repositories():
            try:
                repos = self.get_repository(reponame)
                if repos is not None:
                    repositories.add(repos)
            except TracError:
                pass # Skip invalid repositories
        return repositories

    def reload_repositories(self):
        """Reload the repositories from the providers."""
        with self._lock:
            # FIXME: trac-admin doesn't reload the environment
            self._cache = {}
            self._all_repositories = None
        self.config.touch()     # Force environment reload

    def notify(self, event, reponame, revs):
        """Notify repositories and change listeners about repository events.

        The supported events are the names of the methods defined in the
        `IRepositoryChangeListener` interface.
        """
        self.log.debug("Event %s on %s for changesets %r",
                       event, reponame, revs)

        # Notify a repository by name, and all repositories with the same
        # base, or all repositories by base or by repository dir
        repos = self.get_repository(reponame)
        repositories = []
        if repos:
            base = repos.get_base()
        else:
            dir = os.path.abspath(reponame)
            repositories = self.get_repositories_by_dir(dir)
            if repositories:
                base = None
            else:
                base = reponame
        if base:
            repositories = [r for r in self.get_real_repositories()
                            if r.get_base() == base]
        if not repositories:
            self.log.warn("Found no repositories matching '%s' base.",
                          base or reponame)
            return
        for repos in sorted(repositories, key=lambda r: r.reponame):
            repos.sync()
            for rev in revs:
                args = []
                if event == 'changeset_modified':
                    args.append(repos.sync_changeset(rev))
                try:
                    changeset = repos.get_changeset(rev)
                except NoSuchChangeset:
                    try:
                        repos.sync_changeset(rev)
                        changeset = repos.get_changeset(rev)
                    except NoSuchChangeset:
                        continue
                self.log.debug("Event %s on %s for revision %s",
                               event, repos.reponame or '(default)', rev)
                for listener in self.change_listeners:
                    getattr(listener, event)(repos, changeset, *args)

    def shutdown(self, tid=None):
        """Free `Repository` instances bound to a given thread identifier"""
        if tid:
            assert tid == threading._get_ident()
            with self._lock:
                repositories = self._cache.pop(tid, {})
                for reponame, repos in repositories.iteritems():
                    repos.close()

    # private methods

    def _get_connector(self, rtype):
        """Retrieve the appropriate connector for the given repository type.

        Note that the self._lock must be held when calling this method.
        """
        if self._connectors is None:
            # build an environment-level cache for the preferred connectors
            self._connectors = {}
            for connector in self.connectors:
                for type_, prio in connector.get_supported_types() or []:
                    keep = (connector, prio)
                    if type_ in self._connectors and \
                            prio <= self._connectors[type_][1]:
                        keep = None
                    if keep:
                        self._connectors[type_] = keep
        if rtype in self._connectors:
            connector, prio = self._connectors[rtype]
            if prio >= 0: # no error condition
                return connector
            else:
                raise TracError(
                    _('Unsupported version control system "%(name)s"'
                      ': %(error)s', name=rtype,
                      error=to_unicode(connector.error)))
        else:
            raise TracError(
                _('Unsupported version control system "%(name)s": '
                  'Can\'t find an appropriate component, maybe the '
                  'corresponding plugin was not enabled? ', name=rtype))


class NoSuchChangeset(ResourceNotFound):
    def __init__(self, rev):
        ResourceNotFound.__init__(self,
                                  _('No changeset %(rev)s in the repository',
                                    rev=rev),
                                  _('No such changeset'))


class NoSuchNode(ResourceNotFound):
    def __init__(self, path, rev, msg=None):
        if msg is None:
            msg = _("No node %(path)s at revision %(rev)s", path=path, rev=rev)
        else:
            msg = _("%(msg)s: No node %(path)s at revision %(rev)s",
                    msg=msg, path=path, rev=rev)
        ResourceNotFound.__init__(self, msg, _('No such node'))


class Repository(object):
    """Base class for a repository provided by a version control system."""

    has_linear_changesets = False

    scope = '/'

    def __init__(self, name, params, log):
        """Initialize a repository.

           :param name: a unique name identifying the repository, usually a
                        type-specific prefix followed by the path to the
                        repository.
           :param params: a `dict` of parameters for the repository. Contains
                          the name of the repository under the key "name" and
                          the surrogate key that identifies the repository in
                          the database under the key "id".
           :param log: a logger instance.
        """
        self.name = name
        self.params = params
        self.reponame = params['name']
        self.id = params['id']
        self.log = log
        self.resource = Resource('repository', self.reponame)

    def close(self):
        """Close the connection to the repository."""
        raise NotImplementedError

    def get_base(self):
        """Return the name of the base repository for this repository.

        This function returns the name of the base repository to which scoped
        repositories belong. For non-scoped repositories, it returns the
        repository name.
        """
        return self.name

    def clear(self, youngest_rev=None):
        """Clear any data that may have been cached in instance properties.

        `youngest_rev` can be specified as a way to force the value
        of the `youngest_rev` property (''will change in 0.12'').
        """
        pass

    def sync(self, rev_callback=None, clean=False):
        """Perform a sync of the repository cache, if relevant.

        If given, `rev_callback` must be a callable taking a `rev` parameter.
        The backend will call this function for each `rev` it decided to
        synchronize, once the synchronization changes are committed to the
        cache. When `clean` is `True`, the cache is cleaned first.
        """
        pass

    def sync_changeset(self, rev):
        """Resync the repository cache for the given `rev`, if relevant.

        Returns a "metadata-only" changeset containing the metadata prior to
        the resync, or `None` if the old values cannot be retrieved (typically
        when the repository is not cached).
        """
        return None

    def get_quickjump_entries(self, rev):
        """Generate a list of interesting places in the repository.

        `rev` might be used to restrict the list of available locations,
        but in general it's best to produce all known locations.

        The generated results must be of the form (category, name, path, rev).
        """
        return []

    def get_path_url(self, path, rev):
        """Return the repository URL for the given path and revision.

        The returned URL can be `None`, meaning that no URL has been specified
        for the repository, an absolute URL, or a scheme-relative URL starting
        with `//`, in which case the scheme of the request should be prepended.
        """
        return None

    def get_changeset(self, rev):
        """Retrieve a Changeset corresponding to the given revision `rev`."""
        raise NotImplementedError

    def get_changeset_uid(self, rev):
        """Return a globally unique identifier for the ''rev'' changeset.

        Two changesets from different repositories can sometimes refer to
        the ''very same'' changeset (e.g. the repositories are clones).
        """

    def get_changesets(self, start, stop):
        """Generate Changeset belonging to the given time period (start, stop).
        """
        rev = self.youngest_rev
        while rev:
            chgset = self.get_changeset(rev)
            if chgset.date < start:
                return
            if chgset.date < stop:
                yield chgset
            rev = self.previous_rev(rev)

    def has_node(self, path, rev=None):
        """Tell if there's a node at the specified (path,rev) combination.

        When `rev` is `None`, the latest revision is implied.
        """
        try:
            self.get_node(path, rev)
            return True
        except TracError:
            return False

    def get_node(self, path, rev=None):
        """Retrieve a Node from the repository at the given path.

        A Node represents a directory or a file at a given revision in the
        repository.
        If the `rev` parameter is specified, the Node corresponding to that
        revision is returned, otherwise the Node corresponding to the youngest
        revision is returned.
        """
        raise NotImplementedError

    def get_oldest_rev(self):
        """Return the oldest revision stored in the repository."""
        raise NotImplementedError
    oldest_rev = property(lambda self: self.get_oldest_rev())

    def get_youngest_rev(self):
        """Return the youngest revision in the repository."""
        raise NotImplementedError
    youngest_rev = property(lambda self: self.get_youngest_rev())

    def previous_rev(self, rev, path=''):
        """Return the revision immediately preceding the specified revision.

        If `path` is given, filter out ancestor revisions having no changes
        below `path`.

        In presence of multiple parents, this follows the first parent.
        """
        raise NotImplementedError

    def next_rev(self, rev, path=''):
        """Return the revision immediately following the specified revision.

        If `path` is given, filter out descendant revisions having no changes
        below `path`.

        In presence of multiple children, this follows the first child.
        """
        raise NotImplementedError

    def parent_revs(self, rev):
        """Return a list of parents of the specified revision."""
        parent = self.previous_rev(rev)
        return [parent] if parent is not None else []

    def rev_older_than(self, rev1, rev2):
        """Provides a total order over revisions.

        Return `True` if `rev1` is an ancestor of `rev2`.
        """
        raise NotImplementedError

    def get_path_history(self, path, rev=None, limit=None):
        """Retrieve all the revisions containing this path.

        If given, `rev` is used as a starting point (i.e. no revision
        ''newer'' than `rev` should be returned).
        The result format should be the same as the one of Node.get_history()
        """
        raise NotImplementedError

    def normalize_path(self, path):
        """Return a canonical representation of path in the repos."""
        raise NotImplementedError

    def normalize_rev(self, rev):
        """Return a (unique) canonical representation of a revision.

        It's up to the backend to decide which string values of `rev`
        (usually provided by the user) should be accepted, and how they
        should be normalized. Some backends may for instance want to match
        against known tags or branch names.

        In addition, if `rev` is `None` or '', the youngest revision should
        be returned.
        """
        raise NotImplementedError

    def short_rev(self, rev):
        """Return a compact representation of a revision in the repos."""
        return self.normalize_rev(rev)

    def display_rev(self, rev):
        """Return a representation of a revision in the repos for displaying to
        the user.

        This can be a shortened revision string, e.g. for repositories using
        long hashes.
        """
        return self.normalize_rev(rev)

    def get_changes(self, old_path, old_rev, new_path, new_rev,
                    ignore_ancestry=1):
        """Generates changes corresponding to generalized diffs.

        Generator that yields change tuples (old_node, new_node, kind, change)
        for each node change between the two arbitrary (path,rev) pairs.

        The old_node is assumed to be None when the change is an ADD,
        the new_node is assumed to be None when the change is a DELETE.
        """
        raise NotImplementedError

    def is_viewable(self, perm):
        """Return True if view permission is granted on the repository."""
        return 'BROWSER_VIEW' in perm(self.resource.child('source', '/'))

    can_view = is_viewable  # 0.12 compatibility


class Node(object):
    """Represents a directory or file in the repository at a given revision."""

    DIRECTORY = "dir"
    FILE = "file"

    resource = property(lambda self: Resource('source', self.path,
                                              version=self.rev,
                                              parent=self.repos.resource))

    # created_path and created_rev properties refer to the Node "creation"
    # in the Subversion meaning of a Node in a versioned tree (see #3340).
    #
    # Those properties must be set by subclasses.
    #
    created_rev = None
    created_path = None

    def __init__(self, repos, path, rev, kind):
        assert kind in (Node.DIRECTORY, Node.FILE), \
               "Unknown node kind %s" % kind
        self.repos = repos
        self.path = to_unicode(path)
        self.rev = rev
        self.kind = kind

    def get_content(self):
        """Return a stream for reading the content of the node.

        This method will return `None` for directories.
        The returned object must support a `read([len])` method.
        """
        raise NotImplementedError

    def get_entries(self):
        """Generator that yields the immediate child entries of a directory.

        The entries are returned in no particular order.
        If the node is a file, this method returns `None`.
        """
        raise NotImplementedError

    def get_history(self, limit=None):
        """Provide backward history for this Node.

        Generator that yields `(path, rev, chg)` tuples, one for each revision
        in which the node was changed. This generator will follow copies and
        moves of a node (if the underlying version control system supports
        that), which will be indicated by the first element of the tuple
        (i.e. the path) changing.
        Starts with an entry for the current revision.

        :param limit: if given, yield at most ``limit`` results.
        """
        raise NotImplementedError

    def get_previous(self):
        """Return the change event corresponding to the previous revision.

        This returns a `(path, rev, chg)` tuple.
        """
        skip = True
        for p in self.get_history(2):
            if skip:
                skip = False
            else:
                return p

    def get_annotations(self):
        """Provide detailed backward history for the content of this Node.

        Retrieve an array of revisions, one `rev` for each line of content
        for that node.
        Only expected to work on (text) FILE nodes, of course.
        """
        raise NotImplementedError

    def get_properties(self):
        """Returns the properties (meta-data) of the node, as a dictionary.

        The set of properties depends on the version control system.
        """
        raise NotImplementedError

    def get_content_length(self):
        """The length in bytes of the content.

        Will be `None` for a directory.
        """
        raise NotImplementedError
    content_length = property(lambda self: self.get_content_length())

    def get_content_type(self):
        """The MIME type corresponding to the content, if known.

        Will be `None` for a directory.
        """
        raise NotImplementedError
    content_type = property(lambda self: self.get_content_type())

    def get_name(self):
        return self.path.split('/')[-1]
    name = property(lambda self: self.get_name())

    def get_last_modified(self):
        raise NotImplementedError
    last_modified = property(lambda self: self.get_last_modified())

    isdir = property(lambda self: self.kind == Node.DIRECTORY)
    isfile = property(lambda self: self.kind == Node.FILE)

    def is_viewable(self, perm):
        """Return True if view permission is granted on the node."""
        return ('BROWSER_VIEW' if self.isdir else 'FILE_VIEW') \
               in perm(self.resource)

    can_view = is_viewable  # 0.12 compatibility


class Changeset(object):
    """Represents a set of changes committed at once in a repository."""

    ADD = 'add'
    COPY = 'copy'
    DELETE = 'delete'
    EDIT = 'edit'
    MOVE = 'move'

    # change types which can have diff associated to them
    DIFF_CHANGES = (EDIT, COPY, MOVE) # MERGE
    OTHER_CHANGES = (ADD, DELETE)
    ALL_CHANGES = DIFF_CHANGES + OTHER_CHANGES

    resource = property(lambda self: Resource('changeset', self.rev,
                                              parent=self.repos.resource))

    def __init__(self, repos, rev, message, author, date):
        self.repos = repos
        self.rev = rev
        self.message = message or ''
        self.author = author or ''
        self.date = date

    def get_properties(self):
        """Returns the properties (meta-data) of the node, as a dictionary.

        The set of properties depends on the version control system.

        Warning: this used to yield 4-elements tuple (besides `name` and
        `text`, there were `wikiflag` and `htmlclass` values).
        This is now replaced by the usage of IPropertyRenderer (see #1601).
        """
        return []

    def get_changes(self):
        """Generator that produces a tuple for every change in the changeset.

        The tuple will contain `(path, kind, change, base_path, base_rev)`,
        where `change` can be one of Changeset.ADD, Changeset.COPY,
        Changeset.DELETE, Changeset.EDIT or Changeset.MOVE,
        and `kind` is one of Node.FILE or Node.DIRECTORY.
        The `path` is the targeted path for the `change` (which is
        the ''deleted'' path  for a DELETE change).
        The `base_path` and `base_rev` are the source path and rev for the
        action (`None` and `-1` in the case of an ADD change).
        """
        raise NotImplementedError

    def get_branches(self):
        """Yield branches to which this changeset belong.
        Each branch is given as a pair `(name, head)`, where `name` is
        the branch name and `head` a flag set if the changeset is a head
        for this branch (i.e. if it has no children changeset).
        """
        return []

    def get_tags(self):
        """Yield tags associated with this changeset.

        .. versionadded :: 1.0
        """
        return []

    def is_viewable(self, perm):
        """Return True if view permission is granted on the changeset."""
        return 'CHANGESET_VIEW' in perm(self.resource)

    can_view = is_viewable  # 0.12 compatibility


# Note: Since Trac 0.12, Exception PermissionDenied class is gone,
# and class Authorizer is gone as well.
#
# Fine-grained permissions are now handled via normal permission policies.
