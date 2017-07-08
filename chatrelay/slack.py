from . import servers, BasicFactory
from autobahn.twisted.websocket import WebSocketClientProtocol, WebSocketClientFactory
from autobahn.websocket.util import parse_url as parse_ws_url
from twisted.internet import reactor, ssl
import json
import logging
import re
import requests

logger = logging.getLogger('chatrelay')

def apiurl(tail):
    return 'https://slack.com/api/'+tail

class SlackBot(WebSocketClientProtocol):
    SILENT_IGNORE = ('hello', 'user_typing', 'reconnect_url', 'presence_change')

    def onConnect(self, response):
        logger.debug('{0} :: Connected'.format(self.conf['name']))

    def onOpen(self):
        logger.debug('{0} :: WebSocket opened'.format(self.conf['name']))

    def relay_message(self, destchan, message, fromnick=None, fromserver=None):
        if not self.conf.get('relay_service_messages', True) and not fromnick:
            logger.debug('{0} :: Ignoring service message'.format(self.conf['name']))
            return

        # remove hash mark if configured on other servers
        destchan = destchan.lstrip('#')

        # prepare postMessage request
        params = {
            'token': self.conf['api_token'],
            'channel': destchan,
            'text': message,
            'as_user': True,
        }
        if fromnick:
            # disambiguate sender
            current_users = self._state.users.values()
            if fromnick in current_users:
                fromnick += '_'+fromserver
            while fromnick in current_users:
                fromnick += '_'

            # update request
            params['as_user'] = False
            params['username'] = fromnick

        # send request
        logger.debug('{0} => username={1} channel={2} :{3}'.format(self.conf['name'], fromnick, destchan, message))
        m = requests.get(apiurl('chat.postMessage'), params=params)
        res = m.json()
        if not res['ok']:
            logger.error('>> Failed to post message, response:\n{0}'.format(m.text))
            raise RuntimeError('Failed to post message to {0}'.format(self.conf['name']))

    def _replace_userid(self, m):
        try:
            return '@'+self._state.users[m.group(1)]
        except KeyError:
            logger.warn('{0} :: Unknown user ID for @-mention')
            return '@unknown'

    def _prepare_mtext(self, msg):
        if 'text' in msg:
            msgparts = [msg['text']]
        else:
            logger.warning('>> No "text" for message')
            msgparts = []

        # handle attachments
        for attachment in msg.get('attachments', []):
            msgparts.append(attachment['fallback'])
        mtext = ' | '.join(msgparts)

        # replace any user IDs with username
        mtext = re.sub(r'<@U[A-Z0-9]+\|([^>]+)>', r'@\1', mtext)
        mtext = re.sub(r'<@(U[A-Z0-9]+)>', self._replace_userid, mtext)

        # replace channel IDs
        mtext = re.sub(r'<#C[A-Z0-9]+\|([^>]+)>', r'#\1', mtext)

        # replace emojis
        for name, emoji in self.conf.get('emoji_map', {}).items():
            mtext = mtext.replace(name, emoji)
        return mtext

    def onMessage(self, payload, isBinary):
        if isBinary:
            raise RuntimeError('Slack sent binary websocket message')
        payload = payload.decode('utf-8')
        logger.debug(u'{0} <= {1}'.format(self.conf['name'], payload))
        msg = json.loads(payload)
        mtype = msg['type']
        if mtype in SlackBot.SILENT_IGNORE:
            pass
        elif mtype == 'message':
            # check for ignored messages
            subtype = msg.get('subtype')
            if msg.get('bot_id') == self.conf['bot_id']:
                logger.debug('>> Ignoring own message')
                return
            if msg.get('hidden'):
                logger.debug('>> Ignoring hidden subtype')
                return

            # Obtain channel name and user name
            try:
                channel = self._state.channels[msg['channel']]
            except KeyError:
                logger.debug('>> Unknown channel ID')
                return
            if 'username' in msg:
                user = msg['username']
            elif 'user' in msg:
                user = self._state.users[msg['user']]
            else:
                logger.warn('{0} :: Cannot find user name for message, using default'.format(self.conf['name']))
                user = self.conf['default_username']

            # Prepare message text
            mtext = self._prepare_mtext(msg)
            if not mtext:
                logger.warning('>> Could not find any message to send')
                return
            logger.debug(u'>> Recvd message from {0} to {1}: {2}'.format(user, channel, mtext))

            # Do relay
            try:
                map = self.conf['channel_map'][channel]
                for dest, destchan in map.items():
                    servers[dest].relay_message(destchan, mtext, user, self.conf['name'])
            except KeyError:
                logger.debug('>> Channel {0} not mapped'.format(channel))
                return
        elif mtype == 'user_change' or mtype == 'team_join':
            id = msg['user']['id']
            name = msg['user']['name']
            self._state.users[id] = name
            logger.debug('>> Recvd user: {0} => {1}'.format(id, name))
        elif mtype == 'channel_created' or mtype == 'channel_rename':
            id = msg['channel']['id']
            name = msg['channel']['name']
            self._state.channels[id] = name
            logger.debug('>> Recvd channel: {0} => {1}'.format(id, name))
        else:
            logger.debug('>> Ignoring unhandled type {0}'.format(mtype))

    def onClose(self, wasClean, code, reason):
        logger.debug('{0} :: Closed: wasClean={1} code={2} reason={3}'.format(self.conf['name'], wasClean, code, reason))


class SlackWSFactory(WebSocketClientFactory):
    protocol = SlackBot

    @classmethod
    def init_connection(cls, conf):
        # Remove hash marks from configured channels if present
        channel_map = {}
        for mychannel, map in conf['channel_map'].items():
            mychannel = mychannel.lstrip('#')
            channel_map[mychannel] = map
        conf['channel_map'] = channel_map

        # Get server state and RTM setup info
        rtmc = requests.get(apiurl('rtm.start'), params={'token': conf['api_token']})
        res = rtmc.json()
        if not res['ok']:
            logger.debug('Slack API rtm.start response:\n{0}'.format(rtmc.text))
            raise RuntimeError('Failed to connect to Slack')
        wsurl = res['url']
        logger.debug('{0} :: Got websocket url = {1}'.format(conf['name'], wsurl))
        logger.debug('{0} :: rtm.start response\n{1}'.format(
            conf['name'], json.dumps(res, indent=2, separators=(',', ': '))))
        slackbot_username = conf.get('slackbot_username', conf['default_username'])
        state = State(res, slackbot_username)
        factory = cls(conf, state, wsurl)

        # Start RTM connection
        isSecure, host, port, resource, path, params = parse_ws_url(wsurl)
        logger.debug('{0} :: Connecting to {1}:{2} secure={3}'.format(conf['name'], host, port, isSecure))
        if isSecure:
            reactor.connectSSL(host, port, factory, ssl.ClientContextFactory())
        else:
            reactor.connectTCP(host, port, factory)

    def __init__(self, conf, _state, wsurl):
        self.conf = conf
        self._state = _state
        WebSocketClientFactory.__init__(self, wsurl)

    def buildProtocol(self, addr):
        # Set up websocket protocol
        name = self.conf['name']
        logger.debug('{0} :: buildProtocol'.format(name))
        proto = WebSocketClientFactory.buildProtocol(self, addr)
        proto.conf = self.conf
        proto._state = self._state

        # Register the protocol object globally
        servers[name] = proto

        return proto


class State(object):
    def __init__(self, rtm_start_res, slackbot_username):
        self.users = {'USLACKBOT':slackbot_username}
        for user in rtm_start_res['users']:
            self.users[user['id']] = user['name']

        self.channels = {}
        for channel in rtm_start_res['channels']:
            if channel['is_member']:
                self.channels[channel['id']] = channel['name']
