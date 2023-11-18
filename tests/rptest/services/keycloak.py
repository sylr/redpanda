import json
import os
import requests
import tempfile

from ducktape.services.service import Service
from ducktape.utils.util import wait_until

from keycloak import KeycloakAdmin

KC_INSTALL_DIR = os.path.join('/', 'opt', 'keycloak')
KC_DATA_DIR = os.path.join(KC_INSTALL_DIR, 'data')
KC_VAULT_DIR = os.path.join(KC_DATA_DIR, 'vault')
KC_BIN_DIR = os.path.join(KC_INSTALL_DIR, 'bin')
KC_CONF_DIR = os.path.join(KC_INSTALL_DIR, 'conf')
KC = os.path.join(KC_BIN_DIR, 'kc.sh')
KCADM = os.path.join(KC_BIN_DIR, 'kcadm.sh')
KC_CFG = 'keycloak.conf'
KC_ADMIN = 'admin'
KC_ADMIN_PASSWORD = 'admin'
KC_ROOT_LOG_LEVEL = 'INFO'
KC_LOG_HANDLER = 'console,file'
KC_LOG_FILE = '/var/log/kc.log'
KC_PORT = 8080

DEFAULT_REALM = 'demorealm'

DEFAULT_AT_LIFESPAN_S = 600

START_CMD_TMPL = """
LAUNCH_JBOSS_IN_BACKGROUND=1 \
KEYCLOAK_ADMIN={admin} \
KEYCLOAK_ADMIN_PASSWORD={pw} \
{kc} start-dev &
"""

OIDC_CONFIG_TMPL = """\
http://{host}:{port}/realms/{realm}/.well-known/openid-configuration\
"""

DEFAULT_CONFIG = {
    'http-port': KC_PORT,
    'hostname': None,
    'hostname-port': KC_PORT,
    'http-enabled': True,
    'proxy': 'passthrough',
    'log': KC_LOG_HANDLER,
    'log-file': KC_LOG_FILE,
    'log-level': KC_ROOT_LOG_LEVEL,
}


class KeycloakConfigWriter:
    _cfg = DEFAULT_CONFIG.copy()

    def __init__(self, dest_dir: str = KC_CONF_DIR, extra_cfg: dict = {}):
        self._cfg.update(extra_cfg)
        self._dest_dir = dest_dir

    @property
    def rep(self):
        return json.dumps(self._cfg)

    def write(self, node):
        try:
            node.account.mkdirs(self._dest_dir)
        except:
            pass
        dest_f = os.path.join(self._dest_dir, KC_CFG)
        with tempfile.NamedTemporaryFile(mode='w') as f:
            for k, v in self._cfg.items():
                if v is None:
                    continue
                f.write(f"{k}={str(v).lower() if type(v) is bool else v}\n")
            f.flush()
            node.account.copy_to(f.name, dest_f)
        return dest_f


class OAuthConfig:
    def __init__(self,
                 client_id,
                 client_secret,
                 token_endpoint,
                 scopes=['openid']):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint = token_endpoint
        self.scopes = scopes


class KeycloakAdminClient:
    def __init__(self,
                 logger,
                 server_url,
                 realm,
                 username=KC_ADMIN,
                 password=KC_ADMIN_PASSWORD,
                 access_token_lifespan_s=DEFAULT_AT_LIFESPAN_S):
        self.logger = logger
        self.logger.debug(f"KeycloakAdminClient for {server_url}")
        self.kc_admin = KeycloakAdmin(
            server_url=server_url,
            username=username,
            password=password,
            realm_name='master',
        )
        self.kc_admin.create_realm(
            payload={
                'realm': realm,
                'enabled': True,
                'accessTokenLifespan': access_token_lifespan_s,
            })
        self.kc_admin.realm_name = realm

    def config(self, server_url, username, password, realm):
        self.kc_admin = KeycloakAdmin(server_url=server_url,
                                      username=username,
                                      password=password,
                                      realm_name=realm)

    def create_client(self, client_id, **kwargs):
        rep = {
            'clientId': client_id,
            'enabled': True,
            'serviceAccountsEnabled': True,  # for client credentials grant
        }
        rep.update(kwargs)
        id = self.kc_admin.create_client(payload=rep)
        self.logger.debug(f'client_id: {id}')
        return id

    def generate_client_secret(self, client_id):
        id = self.kc_admin.get_client_id(client_id)
        self.kc_admin.generate_client_secrets(id)

    def get_client_secret(self, client_id):
        id = self.kc_admin.get_client_id(client_id)
        secret = self.kc_admin.get_client_secrets(id)
        return secret['value']

    def create_user(self, username, password, realm_admin=False, **kwargs):
        rep = {
            'username':
            username,
            'credentials': [{
                'type': 'password',
                'value': password,
                'temporary': False,
            }],
            'enabled':
            True
        }
        rep.update(kwargs)

        user_id = self.kc_admin.create_user(rep)

        if realm_admin:
            client_id = self.kc_admin.get_client_id('realm-management')
            role_id = self.kc_admin.get_client_role_id(client_id=client_id,
                                                       role_name='realm-admin')
            self.kc_admin.assign_client_role(user_id=user_id,
                                             client_id=client_id,
                                             roles={
                                                 'name': 'realm-admin',
                                                 'id': role_id
                                             })
        return user_id

    def update_user(self, username, **kwargs):
        user_id = self.kc_admin.get_user_id(username)
        if user_id is None:
            raise Exception(f"User {username} not found")

        self.kc_admin.update_user(user_id=user_id, payload=kwargs)


class KeycloakService(Service):
    logs = {
        'keycloak_log': {
            'path': f"{KC_LOG_FILE}",
            "collect_default": True,
        },
    }

    def __init__(self,
                 context,
                 port=KC_PORT,
                 realm=DEFAULT_REALM,
                 log_level=KC_ROOT_LOG_LEVEL):
        super(KeycloakService, self).__init__(context, num_nodes=1)
        self.realm = realm
        self.http_port = port
        self.log_level = log_level
        self._admin = None
        self._remote_config_file = None

    @property
    def admin(self):
        assert self._admin is not None
        return self._admin

    @property
    def admin_ll(self):
        return self.admin.kc_admin

    def _start_cmd(self):
        cmd = START_CMD_TMPL.format(admin=KC_ADMIN,
                                    pw=KC_ADMIN_PASSWORD,
                                    kc=KC)
        self.logger.debug(f"KC START CMD: {cmd}")
        return cmd

    def host(self, node):
        return node.account.hostname

    def get_discovery_url(self, node):
        return OIDC_CONFIG_TMPL.format(host=self.host(node),
                                       port=self.http_port,
                                       realm=self.realm)

    def get_token_endpoint(self, node):
        oidc_config = requests.get(self.get_discovery_url(node=node)).json()
        return oidc_config['token_endpoint']

    def login_admin_user(self, node, username, password):
        self.admin.config(
            server_url=f'http://{self.host(node)}:{self.http_port}',
            username=username,
            password=password,
            realm=self.realm)

    def generate_oauth_config(self, node, client_id):
        secret = self.admin.get_client_secret(client_id)
        token_endpoint = self.get_token_endpoint(node)
        return OAuthConfig(client_id, secret, token_endpoint)

    def pids(self, node):
        return node.account.java_pids('quarkus')

    def alive(self, node):
        return len(self.pids(node)) > 0

    def start_node(self,
                   node,
                   access_token_lifespan_s=DEFAULT_AT_LIFESPAN_S,
                   **kwargs):

        extra_cfg = {
            'hostname': self.host(node),
            'hostname-port': self.hostname_port,
            'http-port': self.http_port,
            'log-level': self.log_level,
        }
        cfg_writer = KeycloakConfigWriter(extra_cfg=extra_cfg)
        self._remote_config_file = cfg_writer.write(node)

        node.account.ssh(f"touch {KC_LOG_FILE}", allow_fail=False)

        self.logger.debug(f"Starting Keycloak service {cfg_writer.rep}")

        with node.account.monitor_log(KC_LOG_FILE) as monitor:
            node.account.ssh_capture(self._start_cmd(), allow_fail=False)
            monitor.wait_until("Running the server in", timeout_sec=120)

        self.logger.debug(f"Keycloak PIDs: {self.pids(node)}")

        self._admin = KeycloakAdminClient(
            self.logger,
            server_url=f'http://{self.host(node)}:{self.http_port}',
            realm=self.realm,
            username=f'{KC_ADMIN}',
            password=f'{KC_ADMIN_PASSWORD}',
            access_token_lifespan_s=access_token_lifespan_s,
        )

    def stop_node(self, node, clean_shutdown=True):
        s = "TERM" if clean_shutdown else "KILL"
        self.logger.warn(f"Stopping node {node.name}")

        for p in self.pids(node):
            node.account.ssh(f"kill -s {s} {p}", allow_fail=not clean_shutdown)

        wait_until(lambda: not self.alive(node),
                   timeout_sec=30,
                   backoff_sec=.5,
                   err_msg="Keycloak took too long to stop.")

    def clean_node(self, node, **kwargs):
        self.logger.warn(f"Cleaning Keycloak node {node.name}")
        if self.alive(node):
            self.stop_node(node)

        # TODO: this might be overly aggressive
        node.account.ssh(f"rm -rf {KC_LOG_FILE}")
        node.account.ssh(f"rm -rf /opt/keycloak/data/*", allow_fail=False)
        if self._remote_config_file is not None:
            node.account.ssh(f"rm {self._remote_config_file}")
