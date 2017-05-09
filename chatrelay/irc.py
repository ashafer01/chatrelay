from . import servers, TextProto, BasicFactory
from time import time as _time
import collections
import logging
import random
import re

logger = logging.getLogger('bridgerelay')

def time():
    return int(_time())

class IRCLine(object):
    """Represents a parsed IRC line, both server and client protocols"""

    def __init__(self):
        self.prefix = None
        self.cmd = None
        self.args = []
        self.text = ''
        self.raw = None
        self.handle = HandleInfo()

    @classmethod
    def parse(cls, line):
        ret = cls()
        ret.raw = line
        tokens = collections.deque(line.strip().split(' '))
        if tokens[0][0:1] == ':':
            ret.prefix = tokens.popleft()[1:]
            ret.handle = HandleInfo.parse(ret.prefix)
        ret.cmd = tokens.popleft()
        text_words = []
        ontext = False
        for token in tokens:
            if not ontext:
                if token.strip() == '':
                    continue
                if token.lstrip()[0:1] == ':':
                    ontext = True
                    text_words.append(token.lstrip()[1:])
                else:
                    ret.args.append(token.strip())
            else:
                text_words.append(token)
        ret.text = ' '.join(text_words)
        return ret

    def __str__(self):
        if self.raw is None:
            ret = []
            if self.prefix is not None:
                ret.append(':' + self.prefix)
            ret.append(self.cmd)
            ret += self.args
            if self.text is not None:
                ret.append(':' + self.text)
            return ' '.join(ret)
        else:
            return self.raw

class HandleInfo(object):
    """Represents an IRC client handle"""

    def __init__(self):
        self.nick = None
        self.user = None
        self.host = None

    @classmethod
    def parse(cls, handle):
        match = re.search('^([^!]+)!([^@]+)@(.+)$', handle.strip())
        ret = cls()
        if match is not None:
            ret.nick = match.group(1)
            ret.user = match.group(2)
            ret.host = match.group(3)
        return ret

    def __str__(self):
        return '{nick}!{user}@{host}'.format(**self.__dict__)


class IRC(TextProto):
    """IRC client protocol"""

    mirc_colors = ['02','03','04','05','06','07','08','09','10','11','12','13']

    def __init__(self, conf):
        self.conf = conf
        self.nickcolor = {}

    def lineReceived(self, raw_line):
        logger.debug("{0} <= {1}".format(self.conf['name'], raw_line))
        line = IRCLine.parse(raw_line)
        if line.cmd == '001':
            if self.conf['nickserv_pass']:
                self.sendLine('PRIVMSG NickServ :IDENTIFY {0}'.format(self.conf['nickserv_pass']))
            for chan in self.conf['join_channels']:
                self.sendLine('JOIN {0}'.format(chan))
        elif line.cmd == 'PING':
            self.sendLine('PONG :{0}'.format(line.text))
        elif line.cmd == 'PRIVMSG':
            for mychan, map in self.conf['channel_map'].items():
                if mychan in line.args:
                    for dest, destchan in map.items():
                        servers[dest].relay_message(destchan, line.text, line.handle.nick, self.conf['name'])

    def connectionMade(self):
        logger.info('{name} :: Connected'.format(**self.conf))
        self.sendLine('PASS {pass}'.format(**self.conf))
        self.sendLine('NICK {nick}'.format(**self.conf))
        self.sendLine('USER {user} {vhost} {host} :{realname}'.format(**self.conf))

    def relay_message(self, destchan, message, fromnick=None, fromserver=None):
        if fromnick:
            if self.conf['nick_colors']:
                color = self.nickcolor.setdefault(fromnick, random.choice(IRC.mirc_colors))
                nick = '\x03{0}{1}\x03'.format(color, fromnick)
            else:
                nick = fromnick
            message = '<{0}> {1}'.format(nick, message)
        self.sendLine('PRIVMSG {0} :{1}'.format(destchan, message))


class UnrealServ(TextProto):
    """IRC server/services protocol - only tested with UnrealIRCd"""

    def __init__(self, conf):
        self.conf = conf
        self.remote_nicks = set()
        self.nicks = {}

    def connectionMade(self):
        logger.info('{name} :: Connected'.format(**self.conf))
        self.sendLine('PASS :{pass}'.format(**self.conf))
        self.sendLine('PROTOCTL EAUTH={vhost} SID={sid}'.format(**self.conf))
        self.sendLine('PROTOCTL NOQUIT NICKv2 SJOIN SJ3 CLK TKLEXT TKLEXT2 NICKIP ESVID MLOCK EXTSWHOIS')
        self.sendLine('SERVER {vhost} 1 :{desc}'.format(**self.conf))

        uid = self.conf['sid'] + ('0' * 6)
        self.sendLine(':{0} UID {nick} 0 {1} {user} {2} {3} 0 {mode} * * :{realname}'.format(
            self.conf['sid'], time(), self.conf['vhost'], uid, **self.conf['handle']))
        self.nicks[self.conf['handle']['nick'].lower()] = uid
        for chan in self.conf['handle']['join_channels']:
            self.sendLine(':{0} SJOIN {1} {2} :{3}'.format(self.conf['sid'], time(), chan, uid))

        self.sendLine('EOS')

    def lineReceived(self, raw_line):
        logger.debug("{0} <= {1}".format(self.conf['name'], raw_line))
        line = IRCLine.parse(raw_line)
        if line.cmd == 'PING':
            self.sendLine(':{0} PONG {1} :{2}'.format(self.conf['sid'], self.conf['vhost'], line.text))
        elif line.cmd == 'UID':
            self.remote_nicks.add(line.args[0].lower())
        elif line.cmd == 'PRIVMSG':
            for mychan, map in self.conf['channel_map'].items():
                if mychan in line.args:
                    for dest, destchan in map.items():
                        servers[dest].relay_message(destchan, line.text, line.prefix, self.conf['name'])

    def relay_message(self, destchan, message, fromnick=None, fromserver=None):
        if fromnick:
            if fromserver and fromnick.lower() in self.remote_nicks:
                fromnick += '_'+fromserver
            while fromnick.lower() in self.remote_nicks:
                fromnick += '_'
            if fromnick.lower() not in self.nicks:
                uid = self.conf['sid'] + str(len(self.nicks)).zfill(6)
                self.sendLine(':{sid} UID {0} 0 {1} {0} {vhost} {2} 0 +i * * :{0}'.format(
                    fromnick, time(), uid, **self.conf))
                self.nicks[fromnick.lower()] = uid
        else:
            fromnick = self.conf['handle']['nick']
        uid = self.nicks[fromnick.lower()]
        self.sendLine(':{0} PRIVMSG {1} :{2}'.format(uid, destchan, message))


class IRCFactory(BasicFactory):
    protocol = IRC


class UnrealServFactory(BasicFactory):
    protocol = UnrealServ
