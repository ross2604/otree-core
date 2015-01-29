#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import time
import urllib
import uuid
from django.contrib import admin
from django.conf.urls import patterns
from django.http import HttpResponse, HttpResponseBadRequest
from django.contrib.staticfiles.templatetags.staticfiles import (
    static as static_template_tag
)
from django.contrib.auth.decorators import (
    user_passes_test, login_required
)

from django.template.response import TemplateResponse
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse
from django.shortcuts import get_object_or_404

import vanilla
from ordered_set import OrderedSet as oset
import easymoney


import otree.common_internal
from otree.session import (
    create_session, get_session_types_dict, get_session_types_list
)
from otree.views.demo import info_about_session_type
from otree import forms
from otree.views.abstract import GenericWaitPageMixin, AdminSessionPageMixin

import otree.constants
import otree.models.session
from otree.common_internal import add_params_to_url
from otree.common import Currency as c
from otree.common import Money
from otree.views.demo import render_to_start_links_page
from otree.models.session import Session, Participant



def new_tab_link(url, label):
    return '<a href="{}" target="_blank">{}</a>'.format(url, label)

def get_callables(Model):
    '''2015-1-14: deprecated function. needs to exist until we can get rid of admin.py from apps'''
    return []

def get_all_fields(Model, for_export=False):

    first_fields = {
        'Player':
            [
                'id_in_group',
                'role',
            ],
        'Group':
            [
                'id',
            ],
        'Subsession':
            [],
        'Participant':
            [
                '_id_in_session_display',
                'code',
                'label',
                '_pages_completed',
                '_current_app_name',
                '_round_number',
                '_current_page_name',
                'status',
                'last_request_succeeded',
            ],
        'Session':
            [
                'code',
                'session_type_name',
                'label',
                'hidden',
                'start_links_link',
                'participants_table_link',
                'payments_link',
                'is_open',
            ],
    }[Model.__name__]
    first_fields = oset(first_fields)

    last_fields = {
        'Player': [],
        'Group': [],
        'Subsession': [],
        'Participant': [
            'exclude_from_data_analysis',
        ],
        'Session': [
        ],
    }[Model.__name__]
    last_fields = oset(last_fields)


    fields_for_export_but_not_changelist = {
        'Player': {'id', 'label', 'subsession', 'session'},
        'Group': {'id'},
        'Subsession': {'id', 'round_number'},
        'Session': {
            'git_commit_timestamp',
            'fixed_pay',
            'money_per_point',
            'comment',
            '_players_assigned_to_groups',
        },
        'Participant': {
            # 'label',
            'ip_address',
            'time_started',
        },
    }[Model.__name__]

    fields_for_changelist_but_not_export = {
        'Player': set(),
        'Group': {'subsession', 'session'},
        'Subsession': {'session'},
        'Session': {

            'hidden',
        },
        'Participant': {
            'name',
            'session',
            'visited',
            # used to tell how long participant has been on a page
            '_last_page_timestamp',
            'status',
            'last_request_succeeded',
        },
    }[Model.__name__]


    fields_to_exclude_from_export_and_changelist = {
        'Player': {
            '_index_in_game_pages',
            'participant',
            'group',
            'subsession',
            'session',
            'round_number',
        },
        'Group': {
            'subsession',
            'session',
        },
        'Subsession': {
            'code',
            'label',
            'session',
            'session_access_code',
            '_experimenter',
            '_index_in_subsessions',
        },
        'Participant': {
            'id',
            'id_in_session',
            'session',  # because we already filter by session
            '_index_in_subsessions',
            'is_on_wait_page',
            'mturk_assignment_id',
            'mturk_worker_id',
            'vars',
            '_current_form_page_url',
            '_max_page_index',
            '_predetermined_arrival_order',
            '_index_in_pages',
            'visited',  # not necessary because 'status' column includes this
            '_waiting_for_ids',
            '_last_request_timestamp',
        },
        'Session': {
            'mturk_payment_was_sent',

            # can't be shown on change page, because pk not editable?
            'id',
            'session_experimenter',
            'subsession_names',
            'demo_already_used',
            'ready',
            'vars',
            '_pre_create_id',
            # don't hide the code, since it's useful as a checksum
            # (e.g. if you're on the payments page)
        }
    }[Model.__name__]

    if for_export:
        fields_to_exclude = fields_to_exclude_from_export_and_changelist.union(
            fields_for_changelist_but_not_export
        )
    else:
        fields_to_exclude = fields_to_exclude_from_export_and_changelist.union(
            fields_for_export_but_not_changelist
        )

    all_fields_in_model = oset([field.name for field in Model._meta.fields])

    middle_fields = all_fields_in_model - first_fields - last_fields - fields_to_exclude

    return list(first_fields | middle_fields | last_fields)

def get_display_table_rows(app_name, for_export, subsession_pk=None):
    if (not for_export) and (not subsession_pk):
        raise ValueError("if this is for the admin results table, you need to specify a subsession pk")
    models_module = otree.common_internal.get_models_module(app_name)
    Player = models_module.Player
    Group = models_module.Group
    Subsession = models_module.Subsession
    if for_export:
        model_order = [
            Participant,
            Player,
            Group,
            Subsession,
            Session
        ]
    else:
        model_order = [
            Player,
            Group,
            Subsession,
        ]

    # get title row
    all_columns = []
    for Model in model_order:
        field_names = get_all_fields(Model, for_export)
        columns_for_this_model = [(Model, field_name) for field_name in field_names]
        all_columns.extend(columns_for_this_model)

    if subsession_pk:
        players = Player.objects.filter(subsession_id=subsession_pk)
    else:
        players = Player.objects.all()
    session_ids = set([player.session_id for player in players])



    # initialize
    parent_objects = {}

    parent_models = [Model for Model in model_order if Model not in {Player, Session}]

    for Model in parent_models:
        parent_objects[Model] = {obj.pk: obj for obj in Model.objects.filter(session_id__in=session_ids)}

    if Session in model_order:
        parent_objects[Session] = {obj.pk: obj for obj in Session.objects.filter(pk__in=session_ids)}

    all_rows = []
    for player in players:
        row = []
        for column in all_columns:
            Model, field_name = column
            if Model == Player:
                model_instance = player
            else:
                fk_name = Model.__name__.lower()
                parent_object_id = getattr(player, "{}_id".format(fk_name))
                if parent_object_id is None:
                    model_instance = None
                else:
                    model_instance = parent_objects[Model][parent_object_id]

            attr = getattr(model_instance, field_name, '')
            if callable(attr):
                attr = attr()
            row.append(attr)
        all_rows.append(row)

    values_to_replace = {None: '', True: 1, False: 0}

    for row in all_rows:
        for i in range(len(row)):
            value = row[i]
            if value in values_to_replace:
                value = values_to_replace[value]
            elif for_export and isinstance(value, easymoney.Money):
                # remove currency formatting for easier analysis
                value = easymoney.to_dec(value)
            value = unicode(value).encode('UTF-8')
            value = value.replace('\n', ' ').replace('\r', ' ')
            row[i] = value

    column_display_names = ['{}.{}'.format(Model.__name__.lower(), field_name) for (Model, field_name) in all_columns]
    return column_display_names, all_rows



class GlobalSingletonAdmin(admin.ModelAdmin):

    list_display = ['id', 'open_session',
                    'persistent_urls_link', 'mturk_snippet_link']
    list_editable = ['open_session']


    def persistent_urls_link(self, instance):
        return new_tab_link('{}/persistent_urls/'.format(instance.pk), 'Link')
    persistent_urls_link.allow_tags = True
    persistent_urls_link.short_description = "Persistent URLs"

    def persistent_urls(self, request, pk):
        from otree.views.concrete import AssignVisitorToOpenSession
        open_session_base_url = request.build_absolute_uri(
            AssignVisitorToOpenSession.url()
        )
        open_session_example_urls = []
        for i in range(1, 31):
            open_session_example_urls.append(
                add_params_to_url(
                    open_session_base_url,
                    {otree.constants.participant_label: 'P{}'.format(i)}
                )
            )

        return TemplateResponse(
            request,
            'otree/admin/PersistentLabURLs.html',
            {
                'open_session_example_urls': open_session_example_urls,
                'access_code_for_open_session': (
                    otree.constants.access_code_for_open_session
                ),
                'participant_label': otree.constants.participant_label
            }
        )



class MTurkSnippet(vanilla.TemplateView):

    def get(self, request, *args, **kwargs):
        hit_page_js_url = request.build_absolute_uri(
            static_template_tag('otree/js/mturk_hit_page.js')
        )
        from otree.views.concrete import AssignVisitorToOpenSessionMTurk
        open_session_url = request.build_absolute_uri(
            AssignVisitorToOpenSessionMTurk.url()
        )
        context = {'hit_page_js_url': hit_page_js_url,
                   'open_session_url': open_session_url}

        return TemplateResponse(request, 'otree/admin/MTurkSnippet.html',
                                context, content_type='text/plain')


class CreateSessionForm(forms.Form):

    def __init__(self, *args, **kwargs):
        self.session_type = kwargs.pop('session_type')
        super(CreateSessionForm, self).__init__(*args, **kwargs)

    num_participants = forms.IntegerField()

    def clean_num_participants(self):
        lcm = self.session_type.lcm()
        num_participants = self.cleaned_data['num_participants']
        if num_participants % lcm:
            raise forms.ValidationError(
                'Number of participants must be a multiple of {}'.format(lcm)
            )
        return num_participants


class WaitUntilSessionCreated(GenericWaitPageMixin, vanilla.View):

    @classmethod
    def url_pattern(cls):
        return r"^WaitUntilSessionCreated/(?P<session_pre_create_id>.+)/$"

    @classmethod
    def url(cls, pre_create_id):
        return "/WaitUntilSessionCreated/{}/".format(pre_create_id)

    def _is_ready(self):
        return Session.objects.filter(
            _pre_create_id=self._pre_create_id
        ).exists()

    def body_text(self):
        return 'Waiting until session created'

    def _response_when_ready(self):
        session = Session.objects.get(_pre_create_id=self._pre_create_id)
        session_home_url = reverse('session_home', args=(session.pk,))
        return HttpResponseRedirect(session_home_url)

    def dispatch(self, request, *args, **kwargs):
        self._pre_create_id = kwargs['session_pre_create_id']
        return super(WaitUntilSessionCreated, self).dispatch(
            request, *args, **kwargs
        )

    def _get_wait_page(self):
        return TemplateResponse(
            self.request, 'otree/WaitPage.html', {'view': self}
        )


def sleep_then_create_session(**kwargs):
    # hack: this sleep is to prevent locks on SQLite. This gives time to let
    # the page request finish before create_session is called,
    # because creating the session involves a lot of database I/O, which seems
    # to cause locks when multiple threads access at the same time.
    time.sleep(5)

    create_session(**kwargs)



class CreateSession(vanilla.FormView):

    form_class = CreateSessionForm
    template_name = 'otree/admin/CreateSession.html'

    @classmethod
    def url_pattern(cls):
        return r"^create_session/(?P<session_type>.+)/$"

    @classmethod
    def url_name(cls):
        return 'session_create'

    def dispatch(self, request, *args, **kwargs):
        session_type_name = urllib.unquote_plus(kwargs.pop('session_type'))
        self.session_type = get_session_types_dict()[session_type_name]
        return super(CreateSession, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = info_about_session_type(self.session_type)
        kwargs.update(context)
        return super(CreateSession, self).get_context_data(**kwargs)

    def get_form(self, data=None, files=None, **kwargs):
        kwargs['session_type'] = self.session_type
        return super(CreateSession, self).get_form(data, files, **kwargs)

    def form_valid(self, form):
        pre_create_id = uuid.uuid4().hex
        kwargs = {
            'session_type_name': self.session_type.name,
            'num_participants': form.cleaned_data['num_participants'],
            '_pre_create_id': pre_create_id,
        }

        threading.Thread(
            target=sleep_then_create_session,
            kwargs=kwargs,
        ).start()

        return HttpResponseRedirect(WaitUntilSessionCreated.url(pre_create_id))

class SessionTypesToCreate(vanilla.View):

    @classmethod
    def url(cls):
        return "/create_session/"

    @classmethod
    def url_name(cls):
        return 'session_types_create'

    @classmethod
    def url_pattern(cls):
        return r"^create_session/$"


    def get(self, *args, **kwargs):
        session_types_info = []
        for session_type in get_session_types_list():
            session_types_info.append(
                {
                    'display_name': session_type.display_name,
                    'url': '/create_session/{}/'.format(session_type.name),
                }
            )

        return TemplateResponse(self.request,
                                'otree/admin/SessionListing.html',
                                {'session_types_info': session_types_info})



class SessionMonitor(AdminSessionPageMixin, vanilla.TemplateView):

    @classmethod
    def url_name(cls):
        return 'session_monitor'

    def get_context_data(self, **kwargs):

        field_names = get_all_fields(Participant)
        rows = []
        for p in self.session.get_participants():
            row = []
            for fn in field_names:
                attr = getattr(p, fn)
                if callable(attr):
                    attr = attr()
                row.append(attr)
            rows.append(row)

        context = super(SessionMonitor, self).get_context_data(**kwargs)
        context.update({
            'column_names': field_names,
            'rows': rows,
            'session': self.session
        })
        return context


class EditSessionProperties(AdminSessionPageMixin, vanilla.UpdateView):

    model = Session
    fields = [
        'label',
        'experimenter_name',
        'money_per_point',
        'time_scheduled',
        'hidden',
        'fixed_pay',
        'comment',
    ]

    @classmethod
    def url_name(cls):
        return 'session_edit'

    def get_context_data(self, **kwargs):
        context = super(EditSessionProperties, self).get_context_data(**kwargs)
        context.update({
            'participants': self.session.get_participants(),
            'session': self.session
        })
        return context

class SessionPayments(AdminSessionPageMixin, vanilla.TemplateView):

    @classmethod
    def url_name(cls):
        return 'session_payments'

    def get_context_data(self, **kwargs):
        context = super(SessionPayments, self).get_context_data(**kwargs)

        session = self.session
        participants = session.get_participants()
        total_payments = sum(
            participant.total_pay() or c(0) for participant in participants
        ).to_money(session)

        try:
            mean_payment = total_payments / len(participants)
        except ZeroDivisionError:
            mean_payment = Money(0)

        context.update({
            'participants': participants,
            'total_payments': total_payments,
            'mean_payment': mean_payment,
            'session_code': session.code,
            'session_type': session.session_type_name,
            'fixed_pay': session.fixed_pay.to_money(session),
        })

        return context

class SessionStartLinks(AdminSessionPageMixin, vanilla.View):

    @classmethod
    def url_name(cls):
        return 'session_start_links'

    def get(self, request, *args, **kwargs):
        return render_to_start_links_page(request, self.session)


class SessionResults(AdminSessionPageMixin, vanilla.View):

    @classmethod
    def url_name(cls):
        return 'session_results'

    def get(self, request, *args, **kwargs):
        session = self.session

        subsession_headings = []
        column_names = []
        participants = session.get_participants()
        participant_labels = [p._id_in_session_display() for p in participants]
        rows = []

        for subsession in session.get_subsessions():
            app_label = subsession._meta.app_label

            subsession_column_names, subsession_rows = get_display_table_rows(
                subsession._meta.app_label,
                for_export=False,
                subsession_pk=subsession.pk
            )

            column_names.extend(subsession_column_names)
            if not rows:
                rows = subsession_rows
            else:
                for i in range(len(rows)):
                    rows[i].extend(subsession_rows[i])

            colspan = len(subsession_rows[0])

            round_number = subsession.round_number
            if round_number > 1:
                subsession_name = '{} [Round {}]'.format(app_label, round_number)
            else:
                subsession_name = app_label

            subsession_headings.append({
                'subsession_name': subsession_name,
                'colspan': colspan
            })

        # prepend labels to the rows
        for row, participant_label in zip(rows, participant_labels):
            row.insert(0, participant_label)

        return TemplateResponse(
            self.request,
            'otree/admin/SessionResults.html',
            {
                'subsession_headings': subsession_headings,
                'column_names': column_names,
                'rows': rows,
            }
        )

class SessionHome(AdminSessionPageMixin, vanilla.TemplateView):

    @classmethod
    def url_name(cls):
        return 'session_home'

    def get_context_data(self, **kwargs):
        context = super(SessionHome, self).get_context_data(**kwargs)
        context.update({'session': self.session })
        return context



class AdminHome(vanilla.ListView):

    template_name = 'otree/admin/Home.html'

    @classmethod
    def url_pattern(cls):
        return r"^admin_home/$"

    @classmethod
    def url_name(cls):
        return 'admin_home'

    @classmethod
    def url(cls):
        return '/admin_home/'

    def get_queryset(self):
        return Session.objects.filter(hidden=False)
