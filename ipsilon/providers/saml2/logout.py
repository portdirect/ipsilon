# Copyright (C) 2015  Rob Crittenden <rcritten@redhat.com>
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

from ipsilon.providers.common import ProviderPageBase
from ipsilon.providers.common import InvalidRequest
from ipsilon.providers.saml2.sessions import SAMLSessionsContainer
from ipsilon.providers.saml2.auth import UnknownProvider
from ipsilon.util.user import UserSession
import cherrypy
import lasso


class LogoutRequest(ProviderPageBase):
    """
    SP-initiated logout.

    The sequence is:
      - On each logout a new session is created to represent that
        provider
      - Initial logout request is verified and stored in the login
        session
      - If there are other sessions then one is chosen that is not
        the current provider and a logoutRequest is sent
      - When a logoutResponse is received the session is removed
      - When all other sessions but the initial one have been
        logged out then it a final logoutResponse is sent and the
        session removed. At this point the cherrypy session is
        deleted.
    """

    def __init__(self, *args, **kwargs):
        super(LogoutRequest, self).__init__(*args, **kwargs)

    def _handle_logout_request(self, us, logout, saml_sessions, message):
        self.debug('Logout request')

        try:
            logout.processRequestMsg(message)
        except (lasso.ServerProviderNotFoundError,
                lasso.ProfileUnknownProviderError) as e:
            msg = 'Invalid SP [%s] (%r [%r])' % (logout.remoteProviderId,
                                                 e, message)
            self.error(msg)
            raise UnknownProvider(msg)
        except (lasso.ProfileInvalidProtocolprofileError,
                lasso.DsError), e:
            msg = 'Invalid SAML Request: %r (%r [%r])' % (logout.request,
                                                          e, message)
            self.error(msg)
            raise InvalidRequest(msg)
        except lasso.Error, e:
            self.error('SLO unknown error: %s' % message)
            raise cherrypy.HTTPError(400, 'Invalid logout request')

        # TODO: verify that the session index is in the request
        session_indexes = logout.request.sessionIndexes
        self.debug('SLO from %s with %s sessions' %
                   (logout.remoteProviderId, session_indexes))

        session = saml_sessions.find_session_by_provider(
            logout.remoteProviderId)
        if session:
            try:
                logout.setSessionFromDump(session.session.dump())
            except lasso.ProfileBadSessionDumpError as e:
                self.error('loading session failed: %s' % e)
                raise cherrypy.HTTPError(400, 'Invalid logout session')
        else:
            self.error('Logout attempt without being loggged in.')
            raise cherrypy.HTTPError(400, 'Not logged in')

        try:
            logout.validateRequest()
        except lasso.ProfileSessionNotFoundError, e:
            self.error('Logout failed. No sessions for %s' %
                       logout.remoteProviderId)
            raise cherrypy.HTTPError(400, 'Not logged in')
        except lasso.LogoutUnsupportedProfileError:
            self.error('Logout failed. Unsupported profile %s' %
                       logout.remoteProviderId)
            raise cherrypy.HTTPError(400, 'Profile does not support logout')
        except lasso.Error, e:
            self.error('SLO validation failed: %s' % e)
            raise cherrypy.HTTPError(400, 'Failed to validate logout request')

        try:
            logout.buildResponseMsg()
        except lasso.ProfileUnsupportedProfileError:
            self.error('Unsupported profile for %s' % logout.remoteProviderId)
            raise cherrypy.HTTPError(400, 'Profile does not support logout')
        except lasso.Error, e:
            self.error('SLO failed to build logout response: %s' % e)

        session.set_logoutstate(logout.msgUrl, logout.request.id,
                                message)
        saml_sessions.start_logout(session)

        us.save_provider_data('saml2', saml_sessions)

        return

    def _handle_logout_response(self, us, logout, saml_sessions, message,
                                samlresponse):

        self.debug('Logout response')

        try:
            logout.processResponseMsg(message)
        except getattr(lasso, 'ProfileRequestDeniedError',
                       lasso.LogoutRequestDeniedError):
            self.error('Logout request denied by %s' %
                       logout.remoteProviderId)
            # Fall through to next provider
        except (lasso.ProfileInvalidMsgError,
                lasso.LogoutPartialLogoutError) as e:
            self.error('Logout request from %s failed: %s' %
                       (logout.remoteProviderId, e))
        else:
            self.debug('Processing SLO Response from %s' %
                       logout.remoteProviderId)

            self.debug('SLO response to request id %s' %
                       logout.response.inResponseTo)

            saml_sessions = us.get_provider_data('saml2')
            if saml_sessions is None:
                # TODO: return logged out instead
                saml_sessions = SAMLSessionsContainer()

            # TODO: need to log out each SessionIndex?
            session = saml_sessions.find_session_by_provider(
                logout.remoteProviderId)

            if session is not None:
                self.debug('Logout response session logout id is: %s' %
                           session.session_id)
                saml_sessions.remove_session_by_provider(
                    logout.remoteProviderId)
                us.save_provider_data('saml2', saml_sessions)
                user = us.get_user()
                self._audit('Logged out user: %s [%s] from %s' %
                            (user.name, user.fullname,
                             logout.remoteProviderId))
            else:
                self.error('Logout attempt without being loggged in.')
                raise cherrypy.HTTPError(400, 'Not logged in')

        return

    def logout(self, message, relaystate=None, samlresponse=None):
        """
        Handle HTTP Redirect logout. This is an asynchronous logout
        request process that relies on the HTTP agent to forward
        logout requests to any other SP's that are also logged in.

        The basic process is this:
         1. A logout request is received. It is processed and the response
            cached.
         2. If any other SP's have also logged in as this user then the
            first such session is popped off and a logout request is
            generated and forwarded to the SP.
         3. If a logout response is received then the user is marked
            as logged out from that SP.
         Repeat steps 2-3 until only the initial logout request is
         left unhandled, at which time the pre-generated response is sent
         back to the SP that originated the logout request.
        """
        logout = self.cfg.idp.get_logout_handler()

        us = UserSession()

        saml_sessions = us.get_provider_data('saml2')
        if saml_sessions is None:
            # No sessions means nothing to log out
            self.error('Logout attempt without being loggged in.')
            raise cherrypy.HTTPError(400, 'Not logged in')

        self.debug('%d sessions loaded' % saml_sessions.count())
        saml_sessions.dump()

        if lasso.SAML2_FIELD_REQUEST in message:
            self._handle_logout_request(us, logout, saml_sessions, message)
        elif samlresponse:
            self._handle_logout_response(us, logout, saml_sessions, message,
                                         samlresponse)
        else:
            raise cherrypy.HTTPRedirect(400, 'Bad Request. Not a logout ' +
                                        'request or response.')

        # Fall through to handle any remaining sessions.

        # Find the next SP to logout and send a LogoutRequest
        saml_sessions = us.get_provider_data('saml2')
        session = saml_sessions.get_next_logout()
        if session:
            self.debug('Going to log out %s' % session.provider_id)

            try:
                logout.setSessionFromDump(session.session.dump())
            except lasso.ProfileBadSessionDumpError as e:
                self.error('Failed to load session: %s' % e)
                raise cherrypy.HTTPRedirect(400, 'Failed to log out user: %s '
                                            % e)

            logout.initRequest(session.provider_id, lasso.HTTP_METHOD_REDIRECT)

            try:
                logout.buildRequestMsg()
            except lasso.Error, e:
                self.error('failure to build logout request msg: %s' % e)
                raise cherrypy.HTTPRedirect(400, 'Failed to log out user: %s '
                                            % e)

            session.set_logoutstate(logout.msgUrl, logout.request.id, None)
            us.save_provider_data('saml2', saml_sessions)

            self.debug('Request logout ID %s for session ID %s' %
                       (logout.request.id, session.session_id))
            self.debug('Redirecting to another SP to logout on %s at %s' %
                       (logout.remoteProviderId, logout.msgUrl))

            raise cherrypy.HTTPRedirect(logout.msgUrl)

        # Otherwise we're done, respond to the original request using the
        # response we cached earlier.

        saml_sessions = us.get_provider_data('saml2')
        if saml_sessions is None or saml_sessions.count() == 0:
            self.error('Logout attempt without being loggged in.')
            raise cherrypy.HTTPError(400, 'Not logged in')

        try:
            session = saml_sessions.get_last_session()
        except ValueError:
            self.debug('SLO get_last_session() unable to find last session')
            raise cherrypy.HTTPError(400, 'Unable to determine logout state')

        redirect = session.logoutstate.get('relaystate')
        if not redirect:
            redirect = self.basepath

        # Log out of cherrypy session
        user = us.get_user()
        self._audit('Logged out user: %s [%s] from %s' %
                    (user.name, user.fullname,
                     session.provider_id))
        us.logout(user)

        self.debug('SLO redirect to %s' % redirect)

        raise cherrypy.HTTPRedirect(redirect)
