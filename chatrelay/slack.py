from . import servers, BasicFactory
from autobahn.twisted.websocket import WebSocketClientProtocol, WebSocketClientFactory
from twisted.internet import reactor, ssl
import json
import logging
import requests

logger = logging.getLogger('chatrelay')

def apiurl(tail):
    return 'https://slack.com/api/'+tail

class SlackBot(WebSocketClientProtocol):
    def onConnect(self, response):
        logger.debug('{0} :: Connected'.format(self.conf['name']))

    def onOpen(self):
        logger.debug('{0} :: WebSocket opened'.format(self.conf['name']))

    def relay_message(self, destchan, message, fromnick=None, fromserver=None):
        destchan = destchan.lstrip('#')
        params = {
            'token': self.conf['api_token'],
            'channel': destchan,
            'text': message,
            'as_user': True,
        }
        if fromnick:
            current_users = self._state.users.values()
            if fromnick in current_users:
                fromnick += '_'+fromserver
            while fromnick in current_users:
                fromnick += '_'
            params['as_user'] = False
            params['username'] = fromnick
        logger.debug('{0} => username={1} channel={2} :{3}'.format(self.conf['name'], fromnick, destchan, message))
        m = requests.get(apiurl('chat.postMessage'), params=params)
        res = m.json()
        if not res['ok']:
            logger.error('Failed to post message, response:\n{0}'.format(m.text))
            raise RuntimeError('Failed to post message to {0}'.format(self.conf['name']))

    def onMessage(self, payload, isBinary):
        if isBinary:
            raise RuntimeError('Slack sent binary websocket message')
        payload = payload.decode('utf-8')
        logger.debug('{0} <= {1}'.format(self.conf['name'], payload))
        msg = json.loads(payload)
        mtype = msg['type']
        if mtype == 'message':
            if msg.get('subtype') == 'message_changed':
                logger.debug('{0} :: Ignoring message change'.format(self.conf['name']))
                return
            if msg.get('bot_id') == self.conf['bot_id']:
                logger.debug('{0} :: Ignoring own message'.format(self.conf['name']))
                return
            channel = self._state.channels[msg['channel']]
            if 'username' in msg:
                user = msg['username']
            elif 'user' in msg:
                user = self._state.users[msg['user']]
            else:
                raise RuntimeError('Cannot find user name for message')
            msgparts = [msg['text']]
            for attachment in msg.get('attachments', []):
                msgparts.append(attachment['fallback'])
            mtext = ' | '.join(msgparts)
            logger.debug('>> Recvd message from {0} to {1}: {2}'.format(user, channel, mtext))
            try:
                map = self.conf['channel_map'][channel]
                for dest, destchan in map.items():
                    servers[dest].relay_message(destchan, mtext, user, self.conf['name'])
            except KeyError:
                logger.debug('{0} :: Channel {1} not mapped'.format(self.conf['name'], channel))
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
            logger.debug('{0} :: Ignoring unhandled type {1}'.format(self.conf['name'], mtype))

    def onClose(self, wasClean, code, reason):
        logger.debug('SlackBot closed - wasClean={0} code={1} reason={2}'.format(wasClean, code, reason))


class SlackWSFactory(WebSocketClientFactory):
    protocol = SlackBot

    @classmethod
    def init_connection(cls, conf):
        rtmc = requests.get(apiurl('rtm.start'), params={'token': conf['api_token']})
        res = rtmc.json()
        if not res['ok']:
            logger.debug('Slack API rtm.start response:\n{0}'.format(rtmc.text))
            raise RuntimeError('Failed to connect to Slack')

        wsurl = res['url']
        logger.debug('{0} :: Got websocket url = {1}'.format(conf['name'], wsurl))
        logger.debug('{0} :: rtm.start response\n{1}'.format(
            conf['name'], json.dumps(res, indent=2, separators=(',', ': '))))
        factory = cls(conf, State(res), wsurl)

        proto, url = wsurl.split('://', 1)
        if proto == 'ws':
            default_port = 80
        elif proto == 'wss':
            default_port = 443
        else:
            logger.error('Unknown protocol for rtm websocket url')
            raise RuntimeError('Slack server supplied unknown protocol in URL')
        netloc, path = url.split('/', 1)
        hp = netloc.rsplit(':', 1)
        try:
            host, port = hp
            port = int(port)
        except ValueError:
            host = hp[0]
            port = default_port
        logger.debug('{0} :: Connecting to {1}:{2}'.format(conf['name'], host, port))
        if proto == 'ws':
            reactor.connectTCP(host, port, factory)
        elif proto == 'wss':
            reactor.connectSSL(host, port, factory, ssl.ClientContextFactory())

    def __init__(self, conf, _state, wsurl):
        self.conf = conf
        self._state = _state
        WebSocketClientFactory.__init__(self, wsurl)

    def buildProtocol(self, addr):
        proto = WebSocketClientFactory.buildProtocol(self, addr)
        proto.conf = self.conf
        proto._state = self._state
        servers[self.conf['name']] = proto
        return proto


class State(object):
    def __init__(self, rtm_start_res):
        self.users = {'USLACKBOT':'SLACK'}
        for user in rtm_start_res['users']:
            self.users[user['id']] = user['name']

        self.channels = {}
        for channel in rtm_start_res['channels']:
            if channel['is_member']:
                self.channels[channel['id']] = channel['name']
