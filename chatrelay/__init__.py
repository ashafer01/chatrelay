from twisted.internet.protocol import ClientFactory
from twisted.protocols.basic import LineReceiver
from twisted.internet import reactor, ssl
import logging

__all__ = []
logger = logging.getLogger('bridgerelay')

servers = {}

class TextProto(LineReceiver):
    """Basics for a text-based protocol"""

    def sendLine(self, line):
        logger.debug("{0} => {1}".format(self.conf['name'], line))
        LineReceiver.sendLine(self, line)


class BasicFactory(ClientFactory):
    """Common factory functions"""

    @classmethod
    def init_connection(cls, conf):
        if conf['ssl']:
            reactor.connectSSL(conf['host'], conf['port'], cls(conf), ssl.ClientContextFactory())
        else:
            reactor.connectTCP(conf['host'], conf['port'], cls(conf))

    def __init__(self, conf):
        self.conf = conf

    def buildProtocol(self, addr):
        name = self.conf['name']
        logger.debug('{0} :: buildProtocol'.format(name))
        servers[name] = self.protocol(self.conf)
        return servers[name]

    def clientConnectionLost(self, connector, reason):
        logger.error('{0} :: Connection lost ({1})'.format(self.conf['name'], reason))
        try:
            for map in self.conf['channel_map'].values():
                for dest, destchan in map.items():
                    servers[dest].relay_message(destchan, 'Lost connection to {0}, retrying in 30s'.format(self.conf['name']))
        except Exception:
            logger.warn('lost connection sendLine failed')
        finally:
            logger.info('{0} :: Retrying in 30s'.format(self.conf['name']))
            reactor.callLater(30, connector.connect)

    def clientConnectionFailed(self, connector, reason):
        logger.error('{0} :: Connection failed ({1})'.format(self.conf['name'], reason))
        try:
            for map in self.conf['channel_map'].values():
                for dest, destchan in map.items():
                    servers[dest].relay_message(destchan, 'Failed to connect to {0}, retrying in 30s'.format(self.conf['name']))
        except Exception:
            logger.warn('failed connection sendLine failed')
        finally:
            logger.info('{0} :: Retrying in 30s'.format(self.conf['name']))
            reactor.callLater(30, connector.connect)
