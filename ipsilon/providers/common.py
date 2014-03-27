#!/usr/bin/python
#
# Copyright (C) 2014  Simo Sorce <simo@redhat.com>
#
# see file 'COPYING' for use and warranty information
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from ipsilon.util.plugin import PluginLoader, PluginObject
from ipsilon.util.plugin import PluginInstaller
from ipsilon.util.page import Page
import cherrypy


class ProviderException(Exception):

    def __init__(self, message):
        super(ProviderException, self).__init__(message)
        self.message = message

    def __str__(self):
        return repr(self.message)

    def _debug(self, fact):
        if cherrypy.config.get('debug', False):
            cherrypy.log('%s: %s' % (self.__class__.__name__, fact))


class ProviderBase(PluginObject):

    def __init__(self, name, path):
        super(ProviderBase, self).__init__()
        self.name = name
        self.path = path
        self.admin = None

    def _debug(self, fact):
        if cherrypy.config.get('debug', False):
            cherrypy.log(fact)

    def get_tree(self, site):
        raise NotImplementedError

    def enable(self, site):
        plugins = site[FACILITY]
        if self in plugins['enabled']:
            return

        # configure self
        if self.name in plugins['config']:
            self.set_config(plugins['config'][self.name])

        # and add self to the root
        root = plugins['root']
        root.add_subtree(self.name, self.get_tree(site))

        plugins['enabled'].append(self)
        self._debug('IdP Provider enabled: %s' % self.name)

    def disable(self, site):
        plugins = site[FACILITY]
        if self not in plugins['enabled']:
            return

        # remove self to the root
        root = plugins['root']
        root.del_subtree(self.name)

        plugins['enabled'].remove(self)
        self._debug('IdP Provider disabled: %s' % self.name)


class ProviderPageBase(Page):

    def __init__(self, site, config):
        super(ProviderPageBase, self).__init__(site)
        self.plugin_name = config.name
        self.cfg = config

    def GET(self, *args, **kwargs):
        raise cherrypy.HTTPError(501)

    def POST(self, *args, **kwargs):
        raise cherrypy.HTTPError(501)

    def root(self, *args, **kwargs):
        op = getattr(self, cherrypy.request.method, self.GET)
        if callable(op):
            return op(*args, **kwargs)
        else:
            raise cherrypy.HTTPError(405)

    def _debug(self, fact):
        superfact = '%s: %s' % (self.plugin_name, fact)
        super(ProviderPageBase, self)._debug(superfact)

    def _audit(self, fact):
        cherrypy.log('%s: %s' % (self.plugin_name, fact))


FACILITY = 'provider_config'


class LoadProviders(object):

    def __init__(self, root, site):
        loader = PluginLoader(LoadProviders, FACILITY, 'IdpProvider')
        site[FACILITY] = loader.get_plugin_data()
        providers = site[FACILITY]

        available = providers['available'].keys()
        self._debug('Available providers: %s' % str(available))

        providers['root'] = root
        for item in providers['whitelist']:
            self._debug('IdP Provider in whitelist: %s' % item)
            if item not in providers['available']:
                continue
            providers['available'][item].enable(site)

    def _debug(self, fact):
        if cherrypy.config.get('debug', False):
            cherrypy.log(fact)


class ProvidersInstall(object):

    def __init__(self):
        pi = PluginInstaller(ProvidersInstall)
        self.plugins = pi.get_plugins()
