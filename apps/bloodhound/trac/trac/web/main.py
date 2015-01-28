# -*- coding: utf-8 -*-
#
# Copyright (C) 2005-2009 Edgewall Software
# Copyright (C) 2005-2007 Christopher Lenz <cmlenz@gmx.de>
# Copyright (C) 2005 Matthew Good <trac@matt-good.net>
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
#         Matthew Good <trac@matt-good.net>

import cgi
import dircache
import fnmatch
from functools import partial
import gc
import locale
import os
import pkg_resources
from pprint import pformat, pprint
import re
import sys

from genshi.builder import Fragment, tag
from genshi.output import DocType
from genshi.template import TemplateLoader

from trac import __version__ as TRAC_VERSION
from trac.config import BoolOption, ExtensionOption, Option, \
                        OrderedExtensionsOption
from trac.core import *
from trac.env import open_environment
from trac.loader import get_plugin_info, match_plugins_to_frames
from trac.perm import PermissionCache, PermissionError
from trac.resource import ResourceNotFound
from trac.util import arity, get_frame_info, get_last_traceback, hex_entropy, \
                      read_file, safe_repr, translation
from trac.util.concurrency import threading
from trac.util.datefmt import format_datetime, localtz, timezone, user_time
from trac.util.text import exception_to_unicode, shorten_line, to_unicode
from trac.util.translation import _, get_negotiated_locale, has_babel, \
                                  safefmt, tag_
from trac.web.api import *
from trac.web.chrome import Chrome
from trac.web.href import Href
from trac.web.session import Session

#: This URL is used for semi-automatic bug reports (see
#: `send_internal_error`).  Please modify it to point to your own
#: Trac instance if you distribute a patched version of Trac.
default_tracker = 'http://trac.edgewall.org'


class FakeSession(dict):
    sid = None
    def save(self):
        pass


class FakePerm(dict):
    def require(self, *args):
        return False
    def __call__(self, *args):
        return self


class RequestWithSession(Request):
    """A request that saves its associated session when sending the reply."""

    def send_response(self, code=200):
        if code < 400:
            self.session.save()
        super(RequestWithSession, self).send_response(code)


class RequestDispatcher(Component):
    """Web request dispatcher.

    This component dispatches incoming requests to registered
    handlers.  Besides, it also takes care of user authentication and
    request pre- and post-processing.
    """
    required = True

    authenticators = ExtensionPoint(IAuthenticator)
    handlers = ExtensionPoint(IRequestHandler)

    filters = OrderedExtensionsOption('trac', 'request_filters',
                                      IRequestFilter,
        doc="""Ordered list of filters to apply to all requests
            (''since 0.10'').""")

    default_handler = ExtensionOption('trac', 'default_handler',
                                      IRequestHandler, 'WikiModule',
        """Name of the component that handles requests to the base
        URL.

        Options include `TimelineModule`, `RoadmapModule`,
        `BrowserModule`, `QueryModule`, `ReportModule`, `TicketModule`
        and `WikiModule`. The default is `WikiModule`. (''since 0.9'')""")

    default_timezone = Option('trac', 'default_timezone', '',
        """The default timezone to use""")

    default_language = Option('trac', 'default_language', '',
        """The preferred language to use if no user preference has
        been set. (''since 0.12.1'')
        """)

    default_date_format = Option('trac', 'default_date_format', '',
        """The date format. Valid options are 'iso8601' for selecting
        ISO 8601 format, or leave it empty which means the default
        date format will be inferred from the browser's default
        language. (''since 1.0'')
        """)

    use_xsendfile = BoolOption('trac', 'use_xsendfile', 'false',
        """When true, send a `X-Sendfile` header and no content when sending
        files from the filesystem, so that the web server handles the content.
        This requires a web server that knows how to handle such a header,
        like Apache with `mod_xsendfile` or lighttpd. (''since 1.0'')
        """)

    # Public API

    def authenticate(self, req):
        for authenticator in self.authenticators:
            authname = authenticator.authenticate(req)
            if authname:
                return authname
        else:
            return 'anonymous'

    def dispatch(self, req):
        """Find a registered handler that matches the request and let
        it process it.

        In addition, this method initializes the data dictionary
        passed to the the template and adds the web site chrome.
        """
        self.log.debug('Dispatching %r', req)
        chrome = Chrome(self.env)

        # Setup request callbacks for lazily-evaluated properties
        req.callbacks.update({
            'authname': self.authenticate,
            'chrome': chrome.prepare_request,
            'perm': self._get_perm,
            'session': self._get_session,
            'locale': self._get_locale,
            'lc_time': self._get_lc_time,
            'tz': self._get_timezone,
            'form_token': self._get_form_token,
            'use_xsendfile': self._get_use_xsendfile,
        })

        try:
            try:
                # Select the component that should handle the request
                chosen_handler = None
                try:
                    for handler in self.handlers:
                        if handler.match_request(req):
                            chosen_handler = handler
                            break
                    if not chosen_handler:
                        if not req.path_info or req.path_info == '/':
                            chosen_handler = self.default_handler
                    # pre-process any incoming request, whether a handler
                    # was found or not
                    chosen_handler = self._pre_process_request(req,
                                                            chosen_handler)
                except TracError, e:
                    raise HTTPInternalError(e)
                if not chosen_handler:
                    if req.path_info.endswith('/'):
                        # Strip trailing / and redirect
                        target = req.path_info.rstrip('/').encode('utf-8')
                        if req.query_string:
                            target += '?' + req.query_string
                        req.redirect(req.href + target, permanent=True)
                    raise HTTPNotFound('No handler matched request to %s',
                                       req.path_info)

                req.callbacks['chrome'] = partial(chrome.prepare_request,
                                                  handler=chosen_handler)

                # Protect against CSRF attacks: we validate the form token
                # for all POST requests with a content-type corresponding
                # to form submissions
                if req.method == 'POST':
                    ctype = req.get_header('Content-Type')
                    if ctype:
                        ctype, options = cgi.parse_header(ctype)
                    if ctype in ('application/x-www-form-urlencoded',
                                 'multipart/form-data') and \
                            req.args.get('__FORM_TOKEN') != req.form_token:
                        if self.env.secure_cookies and req.scheme == 'http':
                            msg = _('Secure cookies are enabled, you must '
                                    'use https to submit forms.')
                        else:
                            msg = _('Do you have cookies enabled?')
                        raise HTTPBadRequest(_('Missing or invalid form token.'
                                               ' %(msg)s', msg=msg))

                # Process the request and render the template
                resp = chosen_handler.process_request(req)
                if resp:
                    if len(resp) == 2: # old Clearsilver template and HDF data
                        self.log.error("Clearsilver template are no longer "
                                       "supported (%s)", resp[0])
                        raise TracError(
                            _("Clearsilver templates are no longer supported, "
                              "please contact your Trac administrator."))
                    # Genshi
                    template, data, content_type = \
                              self._post_process_request(req, *resp)
                    if 'hdfdump' in req.args:
                        req.perm.require('TRAC_ADMIN')
                        # debugging helper - no need to render first
                        out = StringIO()
                        pprint(data, out)
                        req.send(out.getvalue(), 'text/plain')

                    output = chrome.render_template(req, template, data,
                                                    content_type)
                    req.send(output, content_type or 'text/html')
                else:
                    self._post_process_request(req)
            except RequestDone:
                raise
            except:
                # post-process the request in case of errors
                err = sys.exc_info()
                try:
                    self._post_process_request(req)
                except RequestDone:
                    raise
                except Exception, e:
                    self.log.error("Exception caught while post-processing"
                                   " request: %s",
                                   exception_to_unicode(e, traceback=True))
                raise err[0], err[1], err[2]
        except PermissionError, e:
            raise HTTPForbidden(to_unicode(e))
        except ResourceNotFound, e:
            raise HTTPNotFound(e)
        except TracError, e:
            raise HTTPInternalError(e)

    # Internal methods

    def _get_perm(self, req):
        if isinstance(req.session, FakeSession):
            return FakePerm()
        else:
            return PermissionCache(self.env, self.authenticate(req))

    def _get_session(self, req):
        try:
            return Session(self.env, req)
        except TracError, e:
            self.log.error("can't retrieve session: %s",
                           exception_to_unicode(e))
            return FakeSession()

    def _get_locale(self, req):
        if has_babel:
            preferred = req.session.get('language')
            default = self.env.config.get('trac', 'default_language', '')
            negotiated = get_negotiated_locale([preferred, default] +
                                               req.languages)
            self.log.debug("Negotiated locale: %s -> %s", preferred, negotiated)
            return negotiated

    def _get_lc_time(self, req):
        lc_time = req.session.get('lc_time')
        if not lc_time or lc_time == 'locale' and not has_babel:
            lc_time = self.default_date_format
        if lc_time == 'iso8601':
            return 'iso8601'
        return req.locale

    def _get_timezone(self, req):
        try:
            return timezone(req.session.get('tz', self.default_timezone
                                            or 'missing'))
        except Exception:
            return localtz

    def _get_form_token(self, req):
        """Used to protect against CSRF.

        The 'form_token' is strong shared secret stored in a user
        cookie.  By requiring that every POST form to contain this
        value we're able to protect against CSRF attacks. Since this
        value is only known by the user and not by an attacker.

        If the the user does not have a `trac_form_token` cookie a new
        one is generated.
        """
        if req.incookie.has_key('trac_form_token'):
            return req.incookie['trac_form_token'].value
        else:
            req.outcookie['trac_form_token'] = hex_entropy(24)
            req.outcookie['trac_form_token']['path'] = req.base_path or '/'
            if self.env.secure_cookies:
                req.outcookie['trac_form_token']['secure'] = True
            if sys.version_info >= (2, 6):
                req.outcookie['trac_form_token']['httponly'] = True
            return req.outcookie['trac_form_token'].value

    def _get_use_xsendfile(self, req):
        return self.use_xsendfile

    def _pre_process_request(self, req, chosen_handler):
        for filter_ in self.filters:
            chosen_handler = filter_.pre_process_request(req, chosen_handler)
        return chosen_handler

    def _post_process_request(self, req, *args):
        nbargs = len(args)
        resp = args
        for f in reversed(self.filters):
            # As the arity of `post_process_request` has changed since
            # Trac 0.10, only filters with same arity gets passed real values.
            # Errors will call all filters with None arguments,
            # and results will not be not saved.
            extra_arg_count = arity(f.post_process_request) - 1
            if extra_arg_count == nbargs:
                resp = f.post_process_request(req, *resp)
            elif nbargs == 0:
                f.post_process_request(req, *(None,)*extra_arg_count)
        return resp

_slashes_re = re.compile(r'/+')


def dispatch_request(environ, start_response):
    """Main entry point for the Trac web interface.

    :param environ: the WSGI environment dict
    :param start_response: the WSGI callback for starting the response
    """

    # SCRIPT_URL is an Apache var containing the URL before URL rewriting
    # has been applied, so we can use it to reconstruct logical SCRIPT_NAME
    script_url = environ.get('SCRIPT_URL')
    if script_url is not None:
        path_info = environ.get('PATH_INFO')
        if not path_info:
            environ['SCRIPT_NAME'] = script_url
        else:
            # mod_wsgi squashes slashes in PATH_INFO (!)
            script_url = _slashes_re.sub('/', script_url)
            path_info = _slashes_re.sub('/', path_info)
            if script_url.endswith(path_info):
                environ['SCRIPT_NAME'] = script_url[:-len(path_info)]

    # If the expected configuration keys aren't found in the WSGI environment,
    # try looking them up in the process environment variables
    environ.setdefault('trac.env_path', os.getenv('TRAC_ENV'))
    environ.setdefault('trac.env_parent_dir',
                       os.getenv('TRAC_ENV_PARENT_DIR'))
    environ.setdefault('trac.env_index_template',
                       os.getenv('TRAC_ENV_INDEX_TEMPLATE'))
    environ.setdefault('trac.template_vars',
                       os.getenv('TRAC_TEMPLATE_VARS'))
    environ.setdefault('trac.locale', '')
    environ.setdefault('trac.base_url',
                       os.getenv('TRAC_BASE_URL'))
    environ.setdefault('trac.bootstrap_handler',
                       os.getenv('TRAC_BOOTSTRAP_HANDLER'))

    locale.setlocale(locale.LC_ALL, environ['trac.locale'])

    # Load handler for environment lookup and instantiation of request objects
    from trac.hooks import load_bootstrap_handler
    bootstrap = load_bootstrap_handler(environ['trac.bootstrap_handler'],
                                       environ.get('wsgi.errors'))

    # Determine the environment
    
    env = env_error = None
    try:
        env = bootstrap.open_environment(environ, start_response)
    except RequestDone:
        return []
    except EnvironmentError, e:
        if e.__class__ is EnvironmentError:
            raise
        else:
            env_error = e
    except Exception, e:
        env_error = e
    else:
        try:
            if env.base_url_for_redirect:
                environ['trac.base_url'] = env.base_url
    
            # Web front-end type and version information
            if not hasattr(env, 'webfrontend'):
                mod_wsgi_version = environ.get('mod_wsgi.version')
                if mod_wsgi_version:
                    mod_wsgi_version = (
                            "%s (WSGIProcessGroup %s WSGIApplicationGroup %s)" %
                            ('.'.join([str(x) for x in mod_wsgi_version]),
                             environ.get('mod_wsgi.process_group'),
                             environ.get('mod_wsgi.application_group') or
                             '%{GLOBAL}'))
                    environ.update({
                        'trac.web.frontend': 'mod_wsgi',
                        'trac.web.version': mod_wsgi_version})
                env.webfrontend = environ.get('trac.web.frontend')
                if env.webfrontend:
                    env.systeminfo.append((env.webfrontend,
                                           environ['trac.web.version']))
        except Exception, e:
            env_error = e

    run_once = environ['wsgi.run_once']

    req = None
    if env_error is None:
        try:
            req = bootstrap.create_request(env, environ, start_response) \
                if env is not None else Request(environ, start_response)
        except Exception:
            log = environ.get('wsgi.errors')
            if log:
                log.write("[FAIL] [Trac] Entry point '%s' "
                          "Method 'create_request' Reason %s" %
                          (bootstrap_ep, repr(exception_to_unicode(e))))
    if req is None:
        req = RequestWithSession(environ, start_response)
    translation.make_activable(lambda: req.locale, env.path if env else None)
    try:
        return _dispatch_request(req, env, env_error)
    finally:
        translation.deactivate()
        if env and not run_once:
            env.shutdown(threading._get_ident())
            # Now it's a good time to do some clean-ups
            #
            # Note: enable the '##' lines as soon as there's a suspicion
            #       of memory leak due to uncollectable objects (typically
            #       objects with a __del__ method caught in a cycle)
            #
            ##gc.set_debug(gc.DEBUG_UNCOLLECTABLE)
            unreachable = gc.collect()
            ##env.log.debug("%d unreachable objects found.", unreachable)
            ##uncollectable = len(gc.garbage)
            ##if uncollectable:
            ##    del gc.garbage[:]
            ##    env.log.warn("%d uncollectable objects found.", uncollectable)


def _dispatch_request(req, env, env_error):
    resp = []

    # fixup env.abs_href if `[trac] base_url` was not specified
    if env and not env.abs_href.base:
        env._abs_href = req.abs_href

    try:
        if not env and env_error:
            raise HTTPInternalError(env_error)
        try:
            dispatcher = RequestDispatcher(env)
            dispatcher.dispatch(req)
        except RequestDone:
            pass
        resp = req._response or []
    except HTTPException, e:
        _send_user_error(req, env, e)
    except Exception, e:
        send_internal_error(env, req, sys.exc_info())
    return resp


def _send_user_error(req, env, e):
    # See trac/web/api.py for the definition of HTTPException subclasses.
    if env:
        env.log.warn('[%s] %s' % (req.remote_addr, exception_to_unicode(e)))
    try:
        # We first try to get localized error messages here, but we
        # should ignore secondary errors if the main error was also
        # due to i18n issues
        title = _('Error')
        if e.reason:
            if title.lower() in e.reason.lower():
                title = e.reason
            else:
                title = _('Error: %(message)s', message=e.reason)
    except Exception:
        title = 'Error'
    # The message is based on the e.detail, which can be an Exception
    # object, but not a TracError one: when creating HTTPException,
    # a TracError.message is directly assigned to e.detail
    if isinstance(e.detail, Exception): # not a TracError
        message = exception_to_unicode(e.detail)
    elif isinstance(e.detail, Fragment): # markup coming from a TracError
        message = e.detail
    else:
        message = to_unicode(e.detail)
    data = {'title': title, 'type': 'TracError', 'message': message,
            'frames': [], 'traceback': None}
    if e.code == 403 and req.authname == 'anonymous':
        # TRANSLATOR: ... not logged in, you may want to 'do so' now (link)
        do_so = tag.a(_("do so"), href=req.href.login())
        req.chrome['notices'].append(
            tag_("You are currently not logged in. You may want to "
                 "%(do_so)s now.", do_so=do_so))
    try:
        req.send_error(sys.exc_info(), status=e.code, env=env, data=data)
    except RequestDone:
        pass

def send_internal_error(env, req, exc_info):
    if env:
        env.log.error("Internal Server Error: %s",
                      exception_to_unicode(exc_info[1], traceback=True))
    message = exception_to_unicode(exc_info[1])
    traceback = get_last_traceback()

    frames, plugins, faulty_plugins = [], [], []
    th = 'http://trac-hacks.org'
    has_admin = False
    try:
        has_admin = 'TRAC_ADMIN' in req.perm
    except Exception:
        pass

    tracker = default_tracker
    if has_admin and not isinstance(exc_info[1], MemoryError):
        # Collect frame and plugin information
        frames = get_frame_info(exc_info[2])
        if env:
            plugins = [p for p in get_plugin_info(env)
                       if any(c['enabled']
                              for m in p['modules'].itervalues()
                              for c in m['components'].itervalues())]
            match_plugins_to_frames(plugins, frames)

            # Identify the tracker where the bug should be reported
            faulty_plugins = [p for p in plugins if 'frame_idx' in p]
            faulty_plugins.sort(key=lambda p: p['frame_idx'])
            if faulty_plugins:
                info = faulty_plugins[0]['info']
                if 'trac' in info:
                    tracker = info['trac']
                elif info.get('home_page', '').startswith(th):
                    tracker = th

    def get_description(_):
        if env and has_admin:
            sys_info = "".join("|| '''`%s`''' || `%s` ||\n"
                               % (k, v.replace('\n', '` [[br]] `'))
                               for k, v in env.get_systeminfo())
            sys_info += "|| '''`jQuery`''' || `#JQUERY#` ||\n"
            enabled_plugins = "".join("|| '''`%s`''' || `%s` ||\n"
                                      % (p['name'], p['version'] or _('N/A'))
                                      for p in plugins)
        else:
            sys_info = _("''System information not available''\n")
            enabled_plugins = _("''Plugin information not available''\n")
        return _("""\
==== How to Reproduce ====

While doing a %(method)s operation on `%(path_info)s`, Trac issued an internal error.

''(please provide additional details here)''

Request parameters:
{{{
%(req_args)s
}}}

User agent: `#USER_AGENT#`

==== System Information ====
%(sys_info)s
==== Enabled Plugins ====
%(enabled_plugins)s
==== Python Traceback ====
{{{
%(traceback)s}}}""",
            method=req.method, path_info=req.path_info,
            req_args=pformat(req.args), sys_info=sys_info,
            enabled_plugins=enabled_plugins, traceback=to_unicode(traceback))

    # Generate the description once in English, once in the current locale
    description_en = get_description(lambda s, **kw: safefmt(s, kw))
    try:
        description = get_description(_)
    except Exception:
        description = description_en

    data = {'title': 'Internal Error',
            'type': 'internal', 'message': message,
            'traceback': traceback, 'frames': frames,
            'shorten_line': shorten_line, 'repr': safe_repr,
            'plugins': plugins, 'faulty_plugins': faulty_plugins,
            'tracker': tracker,
            'description': description, 'description_en': description_en}

    try:
        req.send_error(exc_info, status=500, env=env, data=data)
    except RequestDone:
        pass


def send_project_index(environ, start_response, parent_dir=None,
                       env_paths=None):
    req = Request(environ, start_response)

    loadpaths = [pkg_resources.resource_filename('trac', 'templates')]
    if req.environ.get('trac.env_index_template'):
        env_index_template = req.environ['trac.env_index_template']
        tmpl_path, template = os.path.split(env_index_template)
        loadpaths.insert(0, tmpl_path)
    else:
        template = 'index.html'

    data = {'trac': {'version': TRAC_VERSION,
                     'time': user_time(req, format_datetime)},
            'req': req}
    if req.environ.get('trac.template_vars'):
        for pair in req.environ['trac.template_vars'].split(','):
            key, val = pair.split('=')
            data[key] = val
    try:
        href = Href(req.base_path)
        projects = []
        for env_name, env_path in get_environments(environ).items():
            try:
                env = open_environment(env_path,
                                       use_cache=not environ['wsgi.run_once'])
                proj = {
                    'env': env,
                    'name': env.project_name,
                    'description': env.project_description,
                    'href': href(env_name)
                }
            except Exception, e:
                proj = {'name': env_name, 'description': to_unicode(e)}
            projects.append(proj)
        projects.sort(lambda x, y: cmp(x['name'].lower(), y['name'].lower()))

        data['projects'] = projects

        loader = TemplateLoader(loadpaths, variable_lookup='lenient',
                                default_encoding='utf-8')
        tmpl = loader.load(template)
        stream = tmpl.generate(**data)
        if template.endswith('.xml'):
            output = stream.render('xml')
            req.send(output, 'text/xml')
        else:
            output = stream.render('xhtml', doctype=DocType.XHTML_STRICT,
                                   encoding='utf-8')
            req.send(output, 'text/html')

    except RequestDone:
        pass


def get_tracignore_patterns(env_parent_dir):
    """Return the list of patterns from env_parent_dir/.tracignore or
    a default pattern of `".*"` if the file doesn't exist.
    """
    path = os.path.join(env_parent_dir, '.tracignore')
    try:
        lines = [line.strip() for line in read_file(path).splitlines()]
    except IOError:
        return ['.*']
    return [line for line in lines if line and not line.startswith('#')]


def get_environments(environ, warn=False):
    """Retrieve canonical environment name to path mapping.

    The environments may not be all valid environments, but they are
    good candidates.
    """
    env_paths = environ.get('trac.env_paths', [])
    env_parent_dir = environ.get('trac.env_parent_dir')
    if env_parent_dir:
        env_parent_dir = os.path.normpath(env_parent_dir)
        paths = dircache.listdir(env_parent_dir)[:]
        dircache.annotate(env_parent_dir, paths)

        # Filter paths that match the .tracignore patterns
        ignore_patterns = get_tracignore_patterns(env_parent_dir)
        paths = [path[:-1] for path in paths if path[-1] == '/'
                 and not any(fnmatch.fnmatch(path[:-1], pattern)
                             for pattern in ignore_patterns)]
        env_paths.extend(os.path.join(env_parent_dir, project) \
                         for project in paths)
    envs = {}
    for env_path in env_paths:
        env_path = os.path.normpath(env_path)
        if not os.path.isdir(env_path):
            continue
        env_name = os.path.split(env_path)[1]
        if env_name in envs:
            if warn:
                print >> sys.stderr, ('Warning: Ignoring project "%s" since '
                                      'it conflicts with project "%s"'
                                      % (env_path, envs[env_name]))
        else:
            envs[env_name] = env_path
    return envs
