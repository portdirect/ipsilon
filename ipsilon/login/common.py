#!/usr/bin/python
#
# Copyright (C) 2013  Simo Sorce <simo@redhat.com>
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

from ipsilon.util.page import Page
from ipsilon.util.user import UserSession
from ipsilon.util.plugin import PluginLoader, PluginObject
import cherrypy


class LoginManagerBase(PluginObject):

    def __init__(self):
        super(LoginManagerBase, self).__init__()
        self.path = '/'
        self.next_login = None

    def redirect_to_path(self, path):
        base = cherrypy.config.get('base.mount', "")
        raise cherrypy.HTTPRedirect('%s/login/%s' % (base, path))

    def auth_successful(self, username):
        # save ref before calling UserSession login() as it
        # may regenerate the session
        ref = '/idp'
        if 'referral' in cherrypy.session:
            ref = cherrypy.session['referral']

        UserSession().login(username)
        raise cherrypy.HTTPRedirect(ref)

    def auth_failed(self):
        # Just make sure we destroy the session
        UserSession().logout(None)

        if self.next_login:
            return self.redirect_to_path(self.next_login.path)

        # FIXME: show an error page instead
        raise cherrypy.HTTPError(401)


class LoginPageBase(Page):

    def __init__(self, site, mgr):
        super(LoginPageBase, self).__init__(site)
        self.lm = mgr

    def root(self, *args, **kwargs):
        raise cherrypy.HTTPError(500)


FACILITY = 'login_config'


class Login(Page):

    def __init__(self, *args, **kwargs):
        super(Login, self).__init__(*args, **kwargs)
        self.first_login = None

        loader = PluginLoader(Login, FACILITY, 'LoginManager')
        self._site[FACILITY] = loader.get_plugin_data()
        plugins = self._site[FACILITY]

        prev_obj = None
        for item in plugins['available']:
            self._log('Login plugin available: %s' % item)
            if item not in plugins['whitelist']:
                continue
            self._log('Login plugin enabled: %s' % item)
            plugins['enabled'].append(item)
            obj = plugins['available'][item]
            if prev_obj:
                prev_obj.next_login = obj
            else:
                self.first_login = obj
            prev_obj = obj
            if item in plugins['config']:
                obj.set_config(plugins['config'][item])
            self.__dict__[item] = obj.get_tree(self._site)

    def _log(self, fact):
        if cherrypy.config.get('debug', False):
            cherrypy.log(fact)

    def root(self, *args, **kwargs):
        if self.first_login:
            raise cherrypy.HTTPRedirect('%s/login/%s' %
                                        (self.basepath,
                                         self.first_login.path))
        return self._template('login/index.html', title='Login')


class Logout(Page):

    def root(self, *args, **kwargs):
        UserSession().logout(self.user)
        return self._template('logout.html', title='Logout')