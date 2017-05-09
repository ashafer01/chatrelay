from importlib import import_module
from twisted.internet import reactor
import logging
import sys
import yaml

logger = logging.getLogger('chatrelay')
logger.addHandler(logging.NullHandler())
logger.setLevel(logging.DEBUG)

def run():
    """main entry point"""

    try:
        config_fn = sys.argv[1]
    except IndexError:
        config_fn = 'config.yaml'
    print("Using config file {0}".format(config_fn))

    with open(config_fn) as f:
        conf = yaml.load(f)

    if conf['log_level']:
        stderrHandler = logging.StreamHandler()
        stderrHandler.setLevel(getattr(logging, conf['log_level']))
        logger.addHandler(stderrHandler)

    names = set()
    for server in conf['servers']:
        name = server['name']
        if name in names:
            raise RuntimeError('Duplicate server name in {0}: {1}'.format(config_fn, name))
        else:
            names.add(name)
        modname, objname = conf['protocols'][server['protocol']]
        getattr(import_module(modname), objname).init_connection(server)
    reactor.run()

if __name__ == '__main__':
    run()
