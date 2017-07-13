import logging

from . import servers
from copy import deepcopy
from os import fdopen
from tempfile import mkstemp
from twisted.internet import reactor

import synapse.config.homeserver
import synapse.app.homeserver
import synapse.storage.engines
import synapse.crypto
import synapse.storage.prepare_database
import synapse.util.stringutils

# configure synapse base logging
logger = logging.getLogger('synapse')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(name)s - %(message)s'))
handler.setLevel(logging.INFO)
logger.addHandler(handler)

# make noisy synapse loggers quieter
noisy_loggers = ('synapse.storage', 'synapse.handlers.typing', 'synapse.handlers.presence')
for logger_name in noisy_loggers:
    logging.getLogger(logger_name).setLevel(logging.ERROR)

# Use the normal chatrelay logger for our own messages
logger = logging.getLogger('chatrelay')

# The chatrelay matrix config is largely a subset of the actual synapse config.
# We build up the actual synapse config into this dictionary, as it would have been
# parsed from yaml
_synapse_config = {
    'no_tls': True, # TODO
    'web_client': False,
    'report_stats': False,
    'enable_metrics': False,
    'soft_file_limit': 0,
    'database': {
        'name': 'sqlite3',
        'args': {},
    },
    'rc_messages_per_second': 0.2,
    'rc_message_burst_count': 10.0,
    'federation_rc_window_size': 1000,
    'federation_rc_sleep_limit': 10,
    'federation_rc_sleep_delay': 500,
    'federation_rc_reject_limit': 50,
    'federation_rc_concurrent': 3,
    'max_upload_size': '0',
    'max_image_pixels': '0',
    'dynamic_thumbnails': False,
    'thumbnail_sizes':[],
    'url_preview_enabled': False,
    'media_store_path': '/tmp/synapse-media', # shouldn't be used
    'uploads_path': '/tmp/synapse-uploads', # shouldn't be used
    'max_spider_size': '0',
    'enable_registration_captcha': False,
    'recaptcha_public_key': 'YOUR_PUBLIC_KEY',
    'recaptcha_private_key': 'YOUR_PRIVATE_KEY',
    'recaptcha_siteverify_api': '',
    'turn_uris': [],
    'turn_user_lifetime': '0',
    'turn_allow_guests': False,
    'enable_registration': False,
    'allow_guest_access': False,
    'bcrypt_rounds': 12,
    'trusted_third_party_id_servers': [
        'matrix.org',
        'vector.im',
        'riot.im',
    ],
    'key_refresh_interval': '1d',
    'perspectives': {
        'servers': {
            'matrix.org': {
                'verify_keys': {
                    'ed25519:auto': {
                        'key': 'Noi6WqcDj0QmPxCNQqgezwTlBKrfqehY1u2FyWP9uYw',
                    },
                },
            },
        },
    },
    'password_config': {
        'enabled': True,
    },
    'old_signing_keys': {},
}

# The default listener config
# `port` must be specified in the chatrelay config
_base_listener = {
    'tls': False,
    'bind_addresses': [
        '0.0.0.0',
    ],
    'type': 'http',
    'x_forwarded': False,
    'resources': [{
        'names': ['client', 'federation'],
        'compress': False,
    }],
}

class SynapseRelay(object):
    @classmethod
    def init_connection(cls, conf):
        ## prepare config

        # required_keys get copied into the synapse config directly
        required_keys = (
            'server_name',
            'signing_key_path',
        )
        for k in required_keys:
            _synapse_config[k] = conf[k]

        # special cases
        _synapse_config['database']['args']['database'] = conf['db_file']

        k = 'macaroon_secret_key'
        minlen = 50
        try:
            if len(conf[k]) < minlen:
                raise Exception()
            _synapse_config[k] = conf[k]
        except Exception:
            # generate a new key if missing
            fd, fn = mkstemp()
            with fdopen(fd, 'w') as f:
                new_key = synapse.util.stringutils.random_string_with_symbols(minlen)
                f.write('{0}: "{1}"\n'.format(k, new_key))
            raise RuntimeError('Missing {0} from matrix server config, a new key has been '
                'generated in {1}, please copy this into the config and then delete the '
                'file'.format(k, fn))

        # opt_keys are copied directly if set
        opt_keys = ('media_store_path', 'uploads_path')
        for k in opt_keys:
            if k in conf:
                _synapse_config[k] = conf[k]

        # listeners build on _base_listener defined above
        # Only `port` is required to be defined in the chatrelay config
        listeners = []
        for listener_conf in conf['listeners']:
            listener = deepcopy(_base_listener)
            listener.update(listener_conf)
            listeners.append(listener)
        _synapse_config['listeners'] = listeners

        # process dict into synapse's expected object
        synapse_config = synapse.config.homeserver.HomeServerConfig()
        synapse_config.invoke_all('read_config', _synapse_config)

        ## This is mostly a copy/paste from synapse.app.homeserver.setup

        if synapse_config.no_tls:
            tls_ctx_factory = None
        else:
            tls_ctx_factory = synapse.crypto.context_factory.ServerContextFactory(synapse_config)
        db_engine = synapse.storage.engines.create_engine(synapse_config.database_config)
        synapse_config.database_config['args']['cp_openfun'] = db_engine.on_new_connection

        hs = synapse.app.homeserver.SynapseHomeServer(
            synapse_config.server_name,
            db_config=synapse_config.database_config,
            tls_server_context_factory=tls_ctx_factory,
            config=synapse_config,
            version_string='Synapse-chatrelay/1.0',
            database_engine=db_engine,
        )

        db_conn = hs.get_db_conn(run_new_connection=False)
        synapse.storage.prepare_database.prepare_database(db_conn, db_engine, config=synapse_config)
        db_engine.on_new_connection(db_conn)

        hs.run_startup_checks(db_conn, db_engine)

        db_conn.commit()

        hs.setup()
        hs.start_listening()

        def start():
            hs.get_pusherpool().start()
            hs.get_state_handler().start_caching()
            hs.get_datastore().start_profiling()
            hs.get_datastore().start_doing_background_updates()
            hs.get_replication_layer().start_get_pdu_cache()

        reactor.callWhenRunning(start)

        ## END copy/paste

        # instantiate and register the object globally
        servers[conf['name']] = cls(conf, hs)


    def __init__(self, conf, homeserver):
        self.conf = conf
        self.homeserver = homeserver

    def relay_message(self, destchan, message, fromnick=None, fromserver=None):
        if fromnick:
            reg_handler = self.homeserver.get_handlers().registration_handler
            user_id, token = reg_handler.get_or_create_user(
                localpart=fromnick,
                requester=None,
            )
        # TODO
