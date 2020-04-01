#!/bin/env python3
import os
import sys
import time

import requests
import requests.auth


base_url = 'http://nexus:8081'
while True:
    print('Waiting for the Nexus server to be up...')
    try:
        rv = requests.get(base_url, timeout=5)
    except requests.ConnectionError:
        time.sleep(3)
        continue

    if rv.ok:
        print('The Nexus server is now up')
        break
    else:
        print(f'The request to the Nexus server failed with the status code: {rv.status_code}')


admin_password_path = '/nexus-data/admin.password'
if not os.path.exists(admin_password_path):
    print(f'{admin_password_path} is not present. Will skip the running of the script.')
    sys.exit(0)

with open(admin_password_path, 'r') as admin_password_file:
    admin_password = admin_password_file.read()


auth = requests.auth.HTTPBasicAuth('admin', admin_password)
headers = {'Content-Type': 'application/json'}
realms = [
    'NexusAuthenticatingRealm',
    'NexusAuthorizingRealm',
    'NpmToken'
]
print('Configuring the active realms...')
rv_set_realms = requests.put(
    f'{base_url}/service/rest/beta/security/realms/active',
    headers=headers,
    auth=auth,
    json=realms,
    timeout=15,
)

if not rv_set_realms.ok:
    print(
        f'Setting the realms to {realms} failed with the status code: {rv_set_realms.status_code}\n'
        f'The output was: {rv_set_realms.text}',
        file=sys.stderr,
    )
    sys.exit(1)


name = 'configure_nexus'
print(f'Adding the {name} script...')
with open('/src/docker/configure-nexus.groovy', 'r') as script:
    payload = {'name': name, 'type': 'groovy', 'content': script.read()}


rv_script = requests.post(
    f'{base_url}/service/rest/v1/script',
    headers=headers,
    auth=auth,
    json=payload,
    timeout=15,
)
if not rv_script.ok:
    print(
        f'The request to create the {name} script failed with the status '
        f'code: {rv_script.status_code}'
    )
    sys.exit(1)


print(f'Running the {name} script...')
rv_script_run = requests.post(
    f'{base_url}/service/rest/v1/script/{name}/run',
    timeout=15,
    headers=headers,
    auth=auth,
    json={
        'base_url': 'http://localhost:8082',
        'cachito_password': 'cachito',
        'cachito_unprivileged_password': 'cachito_unprivileged',
        'new_admin_password': 'admin',
    },
)

if not rv_script_run.ok:
    print(
        f'Running the {name} script failed with the status code: {rv_script_run.status_code}\n'
        f'The output was: {rv_script_run.text}',
        file=sys.stderr,
    )
    sys.exit(1)
elif os.path.exists(admin_password_path):
    os.remove(admin_password_path)
