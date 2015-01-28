# -*- coding: utf-8 -*-
#
# Copyright (C) 2004-2009 Edgewall Software
# Copyright (C) 2004-2005 Christopher Lenz <cmlenz@gmx.de>
# Copyright (C) 2006-2007 Christian Boos <cboos@edgewall.org>
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

from StringIO import StringIO
from datetime import datetime, timedelta
import re

from genshi.builder import tag

from trac import __version__
from trac.attachment import AttachmentModule
from trac.config import ConfigSection, ExtensionOption
from trac.core import *
from trac.perm import IPermissionRequestor
from trac.resource import *
from trac.search import ISearchSource, search_to_regexps, shorten_result
from trac.util import as_bool
from trac.util.datefmt import parse_date, utc, to_utimestamp, to_datetime, \
                              get_datetime_format_hint, format_date, \
                              format_datetime, from_utimestamp, user_time
from trac.util.text import CRLF
from trac.util.translation import _, tag_
from trac.ticket.api import TicketSystem
from trac.ticket.model import Milestone, MilestoneCache, Ticket, \
                              group_milestones
from trac.timeline.api import ITimelineEventProvider
from trac.web import IRequestHandler, RequestDone
from trac.web.chrome import (Chrome, INavigationContributor,
                             add_link, add_notice, add_script, add_stylesheet,
                             add_warning, auth_link, prevnext_nav, web_context)
from trac.wiki.api import IWikiSyntaxProvider
from trac.wiki.formatter import format_to


class ITicketGroupStatsProvider(Interface):
    def get_ticket_group_stats(ticket_ids):
        """ Gather statistics on a group of tickets.

        This method returns a valid `TicketGroupStats` object.
        """

class TicketGroupStats(object):
    """Encapsulates statistics on a group of tickets."""

    def __init__(self, title, unit):
        """
        :param title: the display name of this group of stats (e.g.
                      ``'ticket status'``)
        :param unit: is the units for these stats in plural form,
                     e.g. ``_('hours'``)
        """
        self.title = title
        self.unit = unit
        self.count = 0
        self.qry_args = {}
        self.intervals = []
        self.done_percent = 0
        self.done_count = 0

    def add_interval(self, title, count, qry_args, css_class,
                     overall_completion=None):
        """Adds a division to this stats' group's progress bar.

        :param title: the display name (e.g. ``'closed'``, ``'spent
                      effort'``) of this interval that will be
                      displayed in front of the unit name
        :param count: the number of units in the interval
        :param qry_args: a dict of extra params that will yield the
                         subset of tickets in this interval on a query.
        :param css_class: is the css class that will be used to
                          display the division
        :param overall_completion: can be set to true to make this
                                   interval count towards overall
                                   completion of this group of
                                   tickets.

        .. versionchanged :: 0.12
           deprecated `countsToProg` argument was removed, use
           `overall_completion` instead
        """
        self.intervals.append({
            'title': title,
            'count': count,
            'qry_args': qry_args,
            'css_class': css_class,
            'percent': None,
            'overall_completion': overall_completion,
        })
        self.count = self.count + count

    def refresh_calcs(self):
        if self.count < 1:
            return
        total_percent = 0
        self.done_percent = 0
        self.done_count = 0
        for interval in self.intervals:
            interval['percent'] = round(float(interval['count'] /
                                        float(self.count) * 100))
            total_percent = total_percent + interval['percent']
            if interval['overall_completion']:
                self.done_percent += interval['percent']
                self.done_count += interval['count']

        # We want the percentages to add up to 100%. To do that, we fudge one
        # of the intervals. If we're <100%, we add to the smallest non-zero
        # interval. If we're >100%, we subtract from the largest interval.
        # The interval is adjusted by enough to make the intervals sum to 100%.
        if self.done_count and total_percent != 100:
            fudge_amt = 100 - total_percent
            fudge_int = [i for i in sorted(self.intervals,
                                           key=lambda k: k['percent'],
                                           reverse=(fudge_amt < 0))
                         if i['percent']][0]
            fudge_int['percent'] += fudge_amt
            self.done_percent += fudge_amt


class DefaultTicketGroupStatsProvider(Component):
    """Configurable ticket group statistics provider.

    See :teo:`TracIni#milestone-groups-section` for a detailed
    example configuration.
    """

    implements(ITicketGroupStatsProvider)

    milestone_groups_section = ConfigSection('milestone-groups',
        """As the workflow for tickets is now configurable, there can
        be many ticket states, and simply displaying closed tickets
        vs. all the others is maybe not appropriate in all cases. This
        section enables one to easily create ''groups'' of states that
        will be shown in different colors in the milestone progress
        bar.

        Note that the groups can only be based on the ticket
        //status//, nothing else. In particular, it's not possible to
        distinguish between different closed tickets based on the
        //resolution//.

        Example configuration with three groups, //closed//, //new//
        and //active// (the default only has closed and active):
        {{{
        # the 'closed' group correspond to the 'closed' tickets
        closed = closed

        # .order: sequence number in the progress bar
        closed.order = 0

        # .query_args: optional parameters for the corresponding
        #              query.  In this example, the changes from the
        #              default are two additional columns ('created' and
        #              'modified'), and sorting is done on 'created'.
        closed.query_args = group=resolution,order=time,col=id,col=summary,col=owner,col=type,col=priority,col=component,col=severity,col=time,col=changetime

        # .overall_completion: indicates groups that count for overall
        #                      completion percentage
        closed.overall_completion = true

        new = new
        new.order = 1
        new.css_class = new
        new.label = new

        # Note: one catch-all group for other statuses is allowed
        active = *
        active.order = 2

        # .css_class: CSS class for this interval
        active.css_class = open

        # .label: displayed label for this group
        active.label = in progress
        }}}

        The definition consists in a comma-separated list of accepted
        status.  Also, '*' means any status and could be used to
        associate all remaining states to one catch-all group.

        The CSS class can be one of: new (yellow), open (no color) or
        closed (green). Other styles can easily be added using custom
        CSS rule: `table.progress td.<class> { background: <color> }`
        to a [TracInterfaceCustomization#SiteAppearance site/style.css] file
        for example.

        (''since 0.11'')""")

    default_milestone_groups =  [
        {'name': 'closed', 'status': 'closed',
         'query_args': 'group=resolution', 'overall_completion': 'true'},
        {'name': 'active', 'status': '*', 'css_class': 'open'}
        ]

    def _get_ticket_groups(self):
        """Returns a list of dict describing the ticket groups
        in the expected order of appearance in the milestone progress bars.
        """
        if 'milestone-groups' in self.config:
            groups = {}
            order = 0
            for groupname, value in self.milestone_groups_section.options():
                qualifier = 'status'
                if '.' in groupname:
                    groupname, qualifier = groupname.split('.', 1)
                group = groups.setdefault(groupname, {'name': groupname,
                                                      'order': order})
                group[qualifier] = value
                order = max(order, int(group['order'])) + 1
            return [group for group in sorted(groups.values(),
                                              key=lambda g: int(g['order']))]
        else:
            return self.default_milestone_groups

    def get_ticket_group_stats(self, ticket_ids):
        total_cnt = len(ticket_ids)
        all_statuses = set(TicketSystem(self.env).get_all_status())
        status_cnt = {}
        for s in all_statuses:
            status_cnt[s] = 0
        if total_cnt:
            for status, count in self.env.db_query("""
                    SELECT status, count(status) FROM ticket
                    WHERE id IN (%s) GROUP BY status
                    """ % ",".join(str(x) for x in sorted(ticket_ids))):
                status_cnt[status] = count

        stat = TicketGroupStats(_('ticket status'), _('tickets'))
        remaining_statuses = set(all_statuses)
        groups =  self._get_ticket_groups()
        catch_all_group = None
        # we need to go through the groups twice, so that the catch up group
        # doesn't need to be the last one in the sequence
        for group in groups:
            status_str = group['status'].strip()
            if status_str == '*':
                if catch_all_group:
                    raise TracError(_(
                        "'%(group1)s' and '%(group2)s' milestone groups "
                        "both are declared to be \"catch-all\" groups. "
                        "Please check your configuration.",
                        group1=group['name'], group2=catch_all_group['name']))
                catch_all_group = group
            else:
                group_statuses = set([s.strip()
                                      for s in status_str.split(',')]) \
                                      & all_statuses
                if group_statuses - remaining_statuses:
                    raise TracError(_(
                        "'%(groupname)s' milestone group reused status "
                        "'%(status)s' already taken by other groups. "
                        "Please check your configuration.",
                        groupname=group['name'],
                        status=', '.join(group_statuses - remaining_statuses)))
                else:
                    remaining_statuses -= group_statuses
                group['statuses'] = group_statuses
        if catch_all_group:
            catch_all_group['statuses'] = remaining_statuses
        for group in groups:
            group_cnt = 0
            query_args = {}
            for s, cnt in status_cnt.iteritems():
                if s in group['statuses']:
                    group_cnt += cnt
                    query_args.setdefault('status', []).append(s)
            for arg in [kv for kv in group.get('query_args', '').split(',')
                        if '=' in kv]:
                k, v = [a.strip() for a in arg.split('=', 1)]
                query_args.setdefault(k, []).append(v)
            stat.add_interval(group.get('label', group['name']),
                              group_cnt, query_args,
                              group.get('css_class', group['name']),
                              as_bool(group.get('overall_completion')))
        stat.refresh_calcs()
        return stat


def get_ticket_stats(provider, tickets):
    return provider.get_ticket_group_stats([t['id'] for t in tickets])

def get_tickets_for_milestone(env, db=None, milestone=None, field='component'):
    """Retrieve all tickets associated with the given `milestone`.

    .. versionchanged :: 1.0
       the `db` parameter is no longer needed and will be removed in
       version 1.1.1
    """
    with env.db_query as db:
        fields = TicketSystem(env).get_ticket_fields()
        if field in [f['name'] for f in fields if not f.get('custom')]:
            sql = """SELECT id, status, %s FROM ticket WHERE milestone=%%s
                     ORDER BY %s""" % (field, field)
            args = (milestone,)
        else:
            sql = """SELECT id, status, value FROM ticket
                       LEFT OUTER JOIN ticket_custom ON (id=ticket AND name=%s)
                      WHERE milestone=%s ORDER BY value"""
            args = (field, milestone)
        return [{'id': tkt_id, 'status': status, field: fieldval}
                for tkt_id, status, fieldval in env.db_query(sql, args)]

def apply_ticket_permissions(env, req, tickets):
    """Apply permissions to a set of milestone tickets as returned by
    `get_tickets_for_milestone()`."""
    return [t for t in tickets
            if 'TICKET_VIEW' in req.perm('ticket', t['id'])]

def milestone_stats_data(env, req, stat, name, grouped_by='component',
                         group=None):
    from trac.ticket.query import QueryModule
    has_query = env[QueryModule] is not None
    def query_href(extra_args):
        if not has_query:
            return None
        args = {'milestone': name, grouped_by: group, 'group': 'status'}
        args.update(extra_args)
        return req.href.query(args)
    return {'stats': stat,
            'stats_href': query_href(stat.qry_args),
            'interval_hrefs': [query_href(interval['qry_args'])
                               for interval in stat.intervals]}

def grouped_stats_data(env, stats_provider, tickets, by, per_group_stats_data):
    """Get the `tickets` stats data grouped by ticket field `by`.

    `per_group_stats_data(gstat, group_name)` should return a data dict to
    include for the group with field value `group_name`.
    """
    group_names = []
    for field in TicketSystem(env).get_ticket_fields():
        if field['name'] == by:
            if 'options' in field:
                group_names = field['options']
                if field.get('optional'):
                    group_names.insert(0, '')
            else:
                group_names = [name for name, in env.db_query("""
                    SELECT DISTINCT COALESCE(%s, '') FROM ticket
                    ORDER BY COALESCE(%s, '')
                    """ % (by, by))]
    max_count = 0
    data = []

    for name in group_names:
        values = (name,) if name else (None, name)
        group_tickets = [t for t in tickets if t[by] in values]
        if not group_tickets:
            continue

        gstat = get_ticket_stats(stats_provider, group_tickets)
        if gstat.count > max_count:
            max_count = gstat.count

        gs_dict = {'name': name}
        gs_dict.update(per_group_stats_data(gstat, name))
        data.append(gs_dict)

    for gs_dict in data:
        percent = 1.0
        if max_count:
            gstat = gs_dict['stats']
            percent = float(gstat.count) / float(max_count) * 100
        gs_dict['percent_of_max_total'] = percent
    return data


class RoadmapModule(Component):
    """Give an overview over all the milestones."""

    implements(INavigationContributor, IPermissionRequestor, IRequestHandler)

    stats_provider = ExtensionOption('roadmap', 'stats_provider',
                                     ITicketGroupStatsProvider,
                                     'DefaultTicketGroupStatsProvider',
        """Name of the component implementing `ITicketGroupStatsProvider`,
        which is used to collect statistics on groups of tickets for display
        in the roadmap views.""")

    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'roadmap'

    def get_navigation_items(self, req):
        if 'ROADMAP_VIEW' in req.perm:
            yield ('mainnav', 'roadmap',
                   tag.a(_('Roadmap'), href=req.href.roadmap(), accesskey=3))

    # IPermissionRequestor methods

    def get_permission_actions(self):
        actions = ['MILESTONE_CREATE', 'MILESTONE_DELETE', 'MILESTONE_MODIFY',
                   'MILESTONE_VIEW', 'ROADMAP_VIEW']
        return ['ROADMAP_VIEW'] + [('ROADMAP_ADMIN', actions)]

    # IRequestHandler methods

    def match_request(self, req):
        return req.path_info == '/roadmap'

    def process_request(self, req):
        req.perm.require('MILESTONE_VIEW')

        show = req.args.getlist('show')
        if 'all' in show:
            show = ['completed']

        milestones = Milestone.select(self.env, 'completed' in show)
        if 'noduedate' in show:
            milestones = [m for m in milestones
                          if m.due is not None or m.completed]
        milestones = [m for m in milestones
                      if 'MILESTONE_VIEW' in req.perm(m.resource)]

        stats = []
        queries = []

        for milestone in milestones:
            tickets = get_tickets_for_milestone(
                    self.env, milestone=milestone.name, field='owner')
            tickets = apply_ticket_permissions(self.env, req, tickets)
            stat = get_ticket_stats(self.stats_provider, tickets)
            stats.append(milestone_stats_data(self.env, req, stat,
                                              milestone.name))
            #milestone['tickets'] = tickets # for the iCalendar view

        if req.args.get('format') == 'ics':
            self._render_ics(req, milestones)
            return

        # FIXME should use the 'webcal:' scheme, probably
        username = None
        if req.authname and req.authname != 'anonymous':
            username = req.authname
        icshref = req.href.roadmap(show=show, user=username, format='ics')
        add_link(req, 'alternate', auth_link(req, icshref), _('iCalendar'),
                 'text/calendar', 'ics')

        data = {
            'milestones': milestones,
            'milestone_stats': stats,
            'queries': queries,
            'show': show,
        }
        add_stylesheet(req, 'common/css/roadmap.css')
        return 'roadmap.html', data, None

    # Internal methods

    def _render_ics(self, req, milestones):
        req.send_response(200)
        req.send_header('Content-Type', 'text/calendar;charset=utf-8')
        buf = StringIO()

        from trac.ticket import Priority
        priorities = {}
        for priority in Priority.select(self.env):
            priorities[priority.name] = float(priority.value)
        def get_priority(ticket):
            value = priorities.get(ticket['priority'])
            if value:
                return int((len(priorities) + 8 * value - 9) /
                       (len(priorities) - 1))

        def get_status(ticket):
            status = ticket['status']
            if status == 'new' or status == 'reopened' and not ticket['owner']:
                return 'NEEDS-ACTION'
            elif status == 'assigned' or status == 'reopened':
                return 'IN-PROCESS'
            elif status == 'closed':
                if ticket['resolution'] == 'fixed':
                    return 'COMPLETED'
                else:
                    return 'CANCELLED'
            else: return ''

        def escape_value(text):
            s = ''.join(map(lambda c: '\\' + c if c in ';,\\' else c, text))
            return '\\n'.join(re.split(r'[\r\n]+', s))

        def write_prop(name, value, params={}):
            text = ';'.join([name] + [k + '=' + v for k, v in params.items()]) \
                 + ':' + escape_value(value)
            firstline = 1
            while text:
                if not firstline:
                    text = ' ' + text
                else:
                    firstline = 0
                buf.write(text[:75] + CRLF)
                text = text[75:]

        def write_date(name, value, params={}):
            params['VALUE'] = 'DATE'
            write_prop(name, format_date(value, '%Y%m%d', req.tz), params)

        def write_utctime(name, value, params={}):
            write_prop(name, format_datetime(value, '%Y%m%dT%H%M%SZ', utc),
                       params)

        host = req.base_url[req.base_url.find('://') + 3:]
        user = req.args.get('user', 'anonymous')

        write_prop('BEGIN', 'VCALENDAR')
        write_prop('VERSION', '2.0')
        write_prop('PRODID', '-//Edgewall Software//NONSGML Trac %s//EN'
                   % __version__)
        write_prop('METHOD', 'PUBLISH')
        write_prop('X-WR-CALNAME',
                   self.env.project_name + ' - ' + _('Roadmap'))
        write_prop('X-WR-CALDESC', self.env.project_description)
        write_prop('X-WR-TIMEZONE', str(req.tz))

        for milestone in milestones:
            uid = '<%s/milestone/%s@%s>' % (req.base_path, milestone.name,
                                            host)
            if milestone.due:
                write_prop('BEGIN', 'VEVENT')
                write_prop('UID', uid)
                write_utctime('DTSTAMP', milestone.due)
                write_date('DTSTART', milestone.due)
                write_prop('SUMMARY', _('Milestone %(name)s',
                                        name=milestone.name))
                write_prop('URL', req.abs_href.milestone(milestone.name))
                if milestone.description:
                    write_prop('DESCRIPTION', milestone.description)
                write_prop('END', 'VEVENT')
            tickets = get_tickets_for_milestone(
                    self.env, milestone=milestone.name, field='owner')
            tickets = apply_ticket_permissions(self.env, req, tickets)
            for tkt_id in [ticket['id'] for ticket in tickets
                           if ticket['owner'] == user]:
                ticket = Ticket(self.env, tkt_id)
                write_prop('BEGIN', 'VTODO')
                write_prop('UID', '<%s/ticket/%s@%s>' % (req.base_path,
                                                         tkt_id, host))
                if milestone.due:
                    write_prop('RELATED-TO', uid)
                    write_date('DUE', milestone.due)
                write_prop('SUMMARY', _('Ticket #%(num)s: %(summary)s',
                                        num=ticket.id,
                                        summary=ticket['summary']))
                write_prop('URL', req.abs_href.ticket(ticket.id))
                write_prop('DESCRIPTION', ticket['description'])
                priority = get_priority(ticket)
                if priority:
                    write_prop('PRIORITY', unicode(priority))
                write_prop('STATUS', get_status(ticket))
                if ticket['status'] == 'closed':
                    for time, in self.env.db_query("""
                            SELECT time FROM ticket_change
                            WHERE ticket=%s AND field='status'
                            ORDER BY time desc LIMIT 1
                            """, (ticket.id,)):
                        write_utctime('COMPLETED', from_utimestamp(time))
                write_prop('END', 'VTODO')
        write_prop('END', 'VCALENDAR')

        ics_str = buf.getvalue().encode('utf-8')
        req.send_header('Content-Length', len(ics_str))
        req.end_headers()
        req.write(ics_str)
        raise RequestDone


class MilestoneModule(Component):
    """View and edit individual milestones."""

    implements(INavigationContributor, IPermissionRequestor, IRequestHandler,
               ITimelineEventProvider, IWikiSyntaxProvider, IResourceManager,
               ISearchSource)

    stats_provider = ExtensionOption('milestone', 'stats_provider',
                                     ITicketGroupStatsProvider,
                                     'DefaultTicketGroupStatsProvider',
        """Name of the component implementing `ITicketGroupStatsProvider`,
        which is used to collect statistics on groups of tickets for display
        in the milestone views.""")


    # INavigationContributor methods

    def get_active_navigation_item(self, req):
        return 'roadmap'

    def get_navigation_items(self, req):
        return []

    # IPermissionRequestor methods

    def get_permission_actions(self):
        actions = ['MILESTONE_CREATE', 'MILESTONE_DELETE', 'MILESTONE_MODIFY',
                   'MILESTONE_VIEW']
        return actions + [('MILESTONE_ADMIN', actions)]

    # ITimelineEventProvider methods

    def get_timeline_filters(self, req):
        if 'MILESTONE_VIEW' in req.perm:
            yield ('milestone', _('Milestones reached'))

    def get_timeline_events(self, req, start, stop, filters):
        if 'milestone' in filters:
            milestone_realm = Resource('milestone')
            for name, due, completed, description \
                    in MilestoneCache(self.env).milestones.itervalues():
                if completed and start <= completed <= stop:
                    # TODO: creation and (later) modifications should also be
                    #       reported
                    milestone = milestone_realm(id=name)
                    if 'MILESTONE_VIEW' in req.perm(milestone):
                        yield ('milestone', completed, '', # FIXME: author?
                               (milestone, description))

            # Attachments
            for event in AttachmentModule(self.env).get_timeline_events(
                req, milestone_realm, start, stop):
                yield event

    def render_timeline_event(self, context, field, event):
        milestone, description = event[3]
        if field == 'url':
            return context.href.milestone(milestone.id)
        elif field == 'title':
            return tag_('Milestone %(name)s completed',
                        name=tag.em(milestone.id))
        elif field == 'description':
            return format_to(self.env, None, context.child(resource=milestone),
                             description)

    # IRequestHandler methods

    def match_request(self, req):
        match = re.match(r'/milestone(?:/(.+))?$', req.path_info)
        if match:
            if match.group(1):
                req.args['id'] = match.group(1)
            return True

    def process_request(self, req):
        milestone_id = req.args.get('id')
        req.perm('milestone', milestone_id).require('MILESTONE_VIEW')

        add_link(req, 'up', req.href.roadmap(), _('Roadmap'))

        action = req.args.get('action', 'view')
        try:
            milestone = Milestone(self.env, milestone_id)
        except ResourceNotFound:
            if 'MILESTONE_CREATE' not in req.perm('milestone', milestone_id):
                raise
            milestone = Milestone(self.env, None)
            milestone.name = milestone_id
            action = 'edit' # rather than 'new' so that it works for POST/save

        if req.method == 'POST':
            if req.args.has_key('cancel'):
                if milestone.exists:
                    req.redirect(req.href.milestone(milestone.name))
                else:
                    req.redirect(req.href.roadmap())
            elif action == 'edit':
                return self._do_save(req, milestone)
            elif action == 'delete':
                self._do_delete(req, milestone)
        elif action in ('new', 'edit'):
            return self._render_editor(req, milestone)
        elif action == 'delete':
            return self._render_confirm(req, milestone)

        if not milestone.name:
            req.redirect(req.href.roadmap())

        return self._render_view(req, milestone)

    # Internal methods

    def _do_delete(self, req, milestone):
        req.perm(milestone.resource).require('MILESTONE_DELETE')

        retarget_to = None
        if req.args.has_key('retarget'):
            retarget_to = req.args.get('target') or None
        milestone.delete(retarget_to, req.authname)
        add_notice(req, _('The milestone "%(name)s" has been deleted.',
                          name=milestone.name))
        req.redirect(req.href.roadmap())

    def _do_save(self, req, milestone):
        if milestone.exists:
            req.perm(milestone.resource).require('MILESTONE_MODIFY')
        else:
            req.perm(milestone.resource).require('MILESTONE_CREATE')

        old_name = milestone.name
        new_name = req.args.get('name')

        milestone.description = req.args.get('description', '')

        if 'due' in req.args:
            due = req.args.get('duedate', '')
            milestone.due = user_time(req, parse_date, due, hint='datetime') \
                            if due else None
        else:
            milestone.due = None

        completed = req.args.get('completeddate', '')
        retarget_to = req.args.get('target')

        # Instead of raising one single error, check all the constraints and
        # let the user fix them by going back to edit mode showing the warnings
        warnings = []
        def warn(msg):
            add_warning(req, msg)
            warnings.append(msg)

        # -- check the name
        # If the name has changed, check that the milestone doesn't already
        # exist
        # FIXME: the whole .exists business needs to be clarified
        #        (#4130) and should behave like a WikiPage does in
        #        this respect.
        try:
            new_milestone = Milestone(self.env, new_name)
            if new_milestone.name == old_name:
                pass        # Creation or no name change
            elif new_milestone.name:
                warn(_('Milestone "%(name)s" already exists, please '
                       'choose another name.', name=new_milestone.name))
            else:
                warn(_('You must provide a name for the milestone.'))
        except ResourceNotFound:
            milestone.name = new_name

        # -- check completed date
        if 'completed' in req.args:
            completed = user_time(req, parse_date, completed,
                                  hint='datetime') if completed else None
            if completed and completed > datetime.now(utc):
                warn(_('Completion date may not be in the future'))
        else:
            completed = None
        milestone.completed = completed

        if warnings:
            return self._render_editor(req, milestone)

        # -- actually save changes
        if milestone.exists:
            milestone.update()
            # eventually retarget opened tickets associated with the milestone
            if 'retarget' in req.args and completed:
                self.env.db_transaction("""
                    UPDATE ticket SET milestone=%s
                    WHERE milestone=%s and status != 'closed'
                    """, (retarget_to, old_name))
                self.log.info("Tickets associated with milestone %s "
                              "retargeted to %s" % (old_name, retarget_to))
        else:
            milestone.insert()

        add_notice(req, _("Your changes have been saved."))
        req.redirect(req.href.milestone(milestone.name))

    def _render_confirm(self, req, milestone):
        req.perm(milestone.resource).require('MILESTONE_DELETE')

        milestones = [m for m in Milestone.select(self.env)
                      if m.name != milestone.name
                      and 'MILESTONE_VIEW' in req.perm(m.resource)]
        data = {
            'milestone': milestone,
            'milestone_groups': group_milestones(milestones,
                'TICKET_ADMIN' in req.perm)
        }
        return 'milestone_delete.html', data, None

    def _render_editor(self, req, milestone):
        # Suggest a default due time of 18:00 in the user's timezone
        now = datetime.now(req.tz)
        default_due = datetime(now.year, now.month, now.day, 18)
        if now.hour > 18:
            default_due += timedelta(days=1)
        default_due = to_datetime(default_due, req.tz)

        data = {
            'milestone': milestone,
            'datetime_hint': get_datetime_format_hint(req.lc_time),
            'default_due': default_due,
            'milestone_groups': [],
        }

        if milestone.exists:
            req.perm(milestone.resource).require('MILESTONE_MODIFY')
            milestones = [m for m in Milestone.select(self.env)
                          if m.name != milestone.name
                          and 'MILESTONE_VIEW' in req.perm(m.resource)]
            data['milestone_groups'] = group_milestones(milestones,
                'TICKET_ADMIN' in req.perm)
        else:
            req.perm(milestone.resource).require('MILESTONE_CREATE')

        chrome = Chrome(self.env)
        chrome.add_jquery_ui(req)
        chrome.add_wiki_toolbars(req)
        return 'milestone_edit.html', data, None

    def _render_view(self, req, milestone):
        milestone_groups = []
        available_groups = []
        component_group_available = False
        ticket_fields = TicketSystem(self.env).get_ticket_fields()

        # collect fields that can be used for grouping
        for field in ticket_fields:
            if field['type'] == 'select' and field['name'] != 'milestone' \
                    or field['name'] in ('owner', 'reporter'):
                available_groups.append({'name': field['name'],
                                         'label': field['label']})
                if field['name'] == 'component':
                    component_group_available = True

        # determine the field currently used for grouping
        by = None
        if component_group_available:
            by = 'component'
        elif available_groups:
            by = available_groups[0]['name']
        by = req.args.get('by', by)

        tickets = get_tickets_for_milestone(self.env, milestone=milestone.name,
                                            field=by)
        tickets = apply_ticket_permissions(self.env, req, tickets)
        stat = get_ticket_stats(self.stats_provider, tickets)

        context = web_context(req, milestone.resource)
        data = {
            'context': context,
            'milestone': milestone,
            'attachments': AttachmentModule(self.env).attachment_data(context),
            'available_groups': available_groups,
            'grouped_by': by,
            'groups': milestone_groups
            }
        data.update(milestone_stats_data(self.env, req, stat, milestone.name))

        if by:
            def per_group_stats_data(gstat, group_name):
                return milestone_stats_data(self.env, req, gstat,
                                            milestone.name, by, group_name)
            milestone_groups.extend(
                grouped_stats_data(self.env, self.stats_provider, tickets, by,
                                   per_group_stats_data))

        add_stylesheet(req, 'common/css/roadmap.css')
        add_script(req, 'common/js/folding.js')

        def add_milestone_link(rel, milestone):
            href = req.href.milestone(milestone.name, by=req.args.get('by'))
            add_link(req, rel, href, _('Milestone "%(name)s"',
                                       name=milestone.name))

        milestones = [m for m in Milestone.select(self.env)
                      if 'MILESTONE_VIEW' in req.perm(m.resource)]
        idx = [i for i, m in enumerate(milestones) if m.name == milestone.name]
        if idx:
            idx = idx[0]
            if idx > 0:
                add_milestone_link('first', milestones[0])
                add_milestone_link('prev', milestones[idx - 1])
            if idx < len(milestones) - 1:
                add_milestone_link('next', milestones[idx + 1])
                add_milestone_link('last', milestones[-1])
        roadmap_back = self.env[RoadmapModule] and _('Back to Roadmap') or None
        prevnext_nav(req, _('Previous Milestone'), _('Next Milestone'),
                         roadmap_back)

        return 'milestone_view.html', data, None

    # IWikiSyntaxProvider methods

    def get_wiki_syntax(self):
        return []

    def get_link_resolvers(self):
        yield ('milestone', self._format_link)

    def _format_link(self, formatter, ns, name, label):
        name, query, fragment = formatter.split_link(name)
        return self._render_link(formatter.context, name, label,
                                 query + fragment)

    def _render_link(self, context, name, label, extra=''):
        try:
            milestone = Milestone(self.env, name)
        except TracError:
            milestone = None
        # Note: the above should really not be needed, `Milestone.exists`
        # should simply be false if the milestone doesn't exist in the db
        # (related to #4130)
        href = context.href.milestone(name)
        if milestone and milestone.exists:
            if 'MILESTONE_VIEW' in context.perm(milestone.resource):
                closed = 'closed ' if milestone.is_completed else ''
                return tag.a(label, class_='%smilestone' % closed,
                             href=href + extra)
        elif 'MILESTONE_CREATE' in context.perm('milestone', name):
            return tag.a(label, class_='missing milestone', href=href + extra,
                         rel='nofollow')
        return tag.a(label, class_='missing milestone')

    # IResourceManager methods

    def get_resource_realms(self):
        yield 'milestone'

    def get_resource_description(self, resource, format=None, context=None,
                                 **kwargs):
        nbhprefix = ResourceSystem(self.env).neighborhood_prefix(
                resource.neighborhood)
        desc = resource.id
        if format != 'compact':
            desc =  nbhprefix + _('Milestone %(name)s', name=resource.id)
        if context:
            return self._render_link(context, resource.id, desc)
        else:
            return desc

    def resource_exists(self, resource):
        """
        >>> from trac.test import EnvironmentStub
        >>> env = EnvironmentStub()

        >>> m1 = Milestone(env)
        >>> m1.name = 'M1'
        >>> m1.insert()

        >>> MilestoneModule(env).resource_exists(Resource('milestone', 'M1'))
        True
        >>> MilestoneModule(env).resource_exists(Resource('milestone', 'M2'))
        False
        """
        return resource.id in MilestoneCache(self.env).milestones

    # ISearchSource methods

    def get_search_filters(self, req):
        if 'MILESTONE_VIEW' in req.perm:
            yield ('milestone', _('Milestones'))

    def get_search_results(self, req, terms, filters):
        if not 'milestone' in filters:
            return
        term_regexps = search_to_regexps(terms)
        milestone_realm = Resource('milestone')
        for name, due, completed, description \
                in MilestoneCache(self.env).milestones.itervalues():
            if any(r.search(description) or r.search(name)
                   for r in term_regexps):
                milestone = milestone_realm(id=name)
                if 'MILESTONE_VIEW' in req.perm(milestone):
                    dt = (completed if completed else
                          due if due else datetime.now(utc))
                    yield (get_resource_url(self.env, milestone, req.href),
                           get_resource_name(self.env, milestone), dt,
                           '', shorten_result(description, terms))

        # Attachments
        for result in AttachmentModule(self.env).get_search_results(
                req, milestone_realm, terms):
            yield result
