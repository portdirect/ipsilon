# Copyright (C) 2016 Ipsilon project Contributors, for license see COPYING

from __future__ import absolute_import

from ipsilon.providers.common import ProviderBase, ProviderInstaller
from ipsilon.providers.openidc.plugins.common import LoadExtensions
from ipsilon.providers.openidc.store import OpenIDCStore
from ipsilon.providers.openidc.auth import OpenIDC
from ipsilon.util.plugin import PluginObject
from ipsilon.util import config as pconfig
from ipsilon.info.common import InfoMapping

import json
from jwcrypto.jwk import JWK, JWKSet
import os
import time
import uuid


class IdpProvider(ProviderBase):

    def __init__(self, *pargs):
        super(IdpProvider, self).__init__('openidc', 'openidc', *pargs)
        self.mapping = InfoMapping()
        self.keyset = None
        self.page = None
        self.datastore = None
        self.server = None
        self.basepath = None
        self.extensions = LoadExtensions()
        self.description = """
Provides OpenID Connect authentication infrastructure. """

        self.new_config(
            self.name,
            pconfig.String(
                'database url',
                'Database URL for OpenID Connect storage',
                'openidc.sqlite'),
            pconfig.Choice(
                'enabled extensions',
                'Choose the extensions to enable',
                self.extensions.available().keys()),
            pconfig.String(
                'endpoint url',
                'The Absolute URL of the OpenID Connect provider',
                'http://localhost:8080/idp/openidc/'),
            pconfig.String(
                'documentation url',
                'The Absolute URL of the OpenID Connect documentation',
                'https://ipsilonproject.org/doc/openidc/'),
            pconfig.String(
                'policy url',
                'The Absolute URL of the OpenID Connect policy',
                'http://www.example.com/'),
            pconfig.String(
                'tos url',
                'The Absolute URL of the OpenID Connect terms of service',
                'http://www.example.com/'),
            pconfig.String(
                'idp key file',
                'The file where the OpenIDC keyset is stored.',
                'openidc.key'),
            pconfig.String(
                'idp sig key id',
                'The key to use for signing.',
                ''),
            pconfig.String(
                'idp subject salt',
                'The salt used for pairwise subjects.',
                None),
            pconfig.Condition(
                'allow dynamic client registration',
                'Allow Dynamic Client registrations for Relying Parties',
                True),
            pconfig.MappingList(
                'default attribute mapping',
                'Defines how to map attributes',
                [['*', '*']]),
            pconfig.ComplexList(
                'default allowed attributes',
                'Defines a list of allowed attributes, applied after mapping',
                ['*']),
        )

    @property
    def endpoint_url(self):
        url = self.get_config_value('endpoint url')
        if url.endswith('/'):
            return url
        else:
            return url+'/'

    @property
    def documentation_url(self):
        url = self.get_config_value('documentation url')
        if url.endswith('/'):
            return url
        else:
            return url+'/'

    @property
    def policy_url(self):
        url = self.get_config_value('policy url')
        if url.endswith('/'):
            return url
        else:
            return url+'/'

    @property
    def tos_url(self):
        url = self.get_config_value('tos url')
        if url.endswith('/'):
            return url
        else:
            return url+'/'

    @property
    def enabled_extensions(self):
        return self.get_config_value('enabled extensions')

    @property
    def idp_key_file(self):
        return self.get_config_value('idp key file')

    @property
    def idp_sig_key_id(self):
        return self.get_config_value('idp sig key id')

    @property
    def idp_subject_salt(self):
        return self.get_config_value('idp subject salt')

    @property
    def allow_dynamic_client_registration(self):
        return self.get_config_value('allow dynamic client registration')

    @property
    def default_attribute_mapping(self):
        return self.get_config_value('default attribute mapping')

    @property
    def default_allowed_attributes(self):
        return self.get_config_value('default allowed attributes')

    @property
    def supported_scopes(self):
        supported = ['openid']
        # Default scopes used in OpenID Connect claims
        supported.extend(['profile', 'email', 'address', 'phone'])
        for _, ext in self.extensions.available().items():
            supported.extend(ext.get_scopes())
        return supported

    def get_tree(self, site):
        self.page = OpenIDC(site, self)
        # self.admin = AdminPage(site, self)

        return self.page

    def used_datastores(self):
        return [self.datastore]

    def init_idp(self):
        self.keyset = JWKSet()
        with open(self.idp_key_file, 'r') as keyfile:
            loaded_keys = json.loads(keyfile.read())
            for key in loaded_keys['keys']:
                self.keyset.add(JWK(**key))

        self.datastore = OpenIDCStore(self.get_config_value('database url'))

    def openid_connect_issuer_wf_rel(self, resource):
        link = {
            'rel': 'http://openid.net/specs/connect/1.0/issuer',
            'href': self.endpoint_url
        }
        return {'links': [link]}

    def on_enable(self):
        super(IdpProvider, self).on_enable()
        self.init_idp()
        self.extensions.enable(self._config['enabled extensions'].get_value(),
                               self)
        self._root.webfinger.register_rel(
            'http://openid.net/specs/connect/1.0/issuer',
            self.openid_connect_issuer_wf_rel
        )

    def on_disable(self):
        super(IdpProvider, self).on_enable()
        self._root.webfinger.unregister_rel(
            'http://openid.net/specs/connect/1.0/issuer'
        )


class Installer(ProviderInstaller):

    def __init__(self, *pargs):
        super(Installer, self).__init__()
        self.name = 'openidc'
        self.pargs = pargs

    def install_args(self, group):
        group.add_argument('--openidc', choices=['yes', 'no'], default='yes',
                           help='Configure OpenID Connect Provider')
        group.add_argument('--openidc-dburi',
                           help='OpenID Connect database URI')
        group.add_argument('--openidc-subject-salt', default=None,
                           help='Salt to use for pairwise subject subjects')
        group.add_argument('--openidc-extensions', default='',
                           help='List of OpenID Connect Extensions to enable')

    def configure(self, opts, changes):
        if opts['openidc'] != 'yes':
            return

        path = os.path.join(opts['data_dir'], 'openidc')
        if not os.path.exists(path):
            os.makedirs(path, 0700)

        keyfile = os.path.join(path, 'openidc.key')
        keyid = int(time.time())
        keyset = JWKSet()
        # We generate one RSA2048 signing key
        rsasig = JWK(generate='RSA', size=2048, use='sig',
                     kid='%s-sig' % keyid)
        keyset.add(rsasig)
        # We generate one RSA2048 encryption key
        rsasig = JWK(generate='RSA', size=2048, use='enc',
                     kid='%s-enc' % keyid)
        keyset.add(rsasig)

        with open(keyfile, 'w') as m:
            m.write(keyset.export())

        proto = 'https'
        url = '%s://%s/%s/openidc/' % (
            proto, opts['hostname'], opts['instance'])

        subject_salt = uuid.uuid4().hex
        if opts['openidc_subject_salt']:
            subject_salt = opts['openidc_subject_salt']

        # Add configuration data to database
        po = PluginObject(*self.pargs)
        po.name = 'openidc'
        po.wipe_data()
        po.wipe_config_values()
        config = {'endpoint url': url,
                  'database url': opts['openidc_dburi'] or
                  opts['database_url'] % {
                      'datadir': opts['data_dir'], 'dbname': 'openidc'},
                  'enabled extensions': opts['openidc_extensions'],
                  'idp key file': keyfile,
                  'idp sig key id': '%s-sig' % keyid,
                  'idp subject salt': subject_salt}
        po.save_plugin_config(config)

        # Update global config to add login plugin
        po.is_enabled = True
        po.save_enabled_state()
