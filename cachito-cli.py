#!/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import os.path
import tarfile

import docker
import requests


logger = logging.getLogger('cachito')


def main():
    remote = {
        'server': 'github.com',
        'repo': 'release-engineering/retrodep',
        'ref': 'master',
    }
    source_path = download_remote(remote)
    logger.debug('source at %s', source_path)

    cache_path = os.path.join('dest', 'cache')
    resolve_dependencies(source_path, cache_path)


def download_remote(remote, dest_dir='dest'):
    if remote['server'] == 'github.com':
        url = 'https://github.com/{repo}/archive/{ref}.tar.gz'.format(**remote)
    else:
        raise NotImplementedError('{server} is not supported'.format(**remote))
    logging.debug('url is %s', url)

    try:
        os.makedirs(dest_dir)
    except FileExistsError:
        pass
    archive_path = os.path.join(dest_dir, 'source.tar.gz')

    response = requests.get(url)
    response.raise_for_status()
    open(archive_path, 'wb').write(response.content)
    return expand_archive(archive_path, os.path.join(dest_dir, 'src'))


def expand_archive(archive_path, dest_dir):
    try:
        os.makedirs(dest_dir)
    except FileExistsError:
        pass

    with tarfile.open(archive_path) as tar:
        tar.extractall(path=dest_dir)

    # Gihub's archive contains a subdir with repo name and commit ID, let's
    # grab that folder.
    # TODO: This, of course, only works for github right now.
    for subdir in os.listdir(dest_dir):
        final_dest_dir = os.path.join(dest_dir, subdir)
        if os.path.isdir(final_dest_dir):
            return final_dest_dir

    raise RuntimeError('Oh no! Subdir not found in archive!')


def resolve_dependencies(source_path, cache_path):
    img = 'golang:1.11'
    client = docker.from_env()
    output = client.containers.run(
        img,
        command='go list -m all',
        tty=True,
        remove=True,
        volumes={
            os.path.realpath(source_path): {'bind': '/usr/src', 'mode': 'Z'},
            os.path.realpath(cache_path): {'bind': '/go/pkg/mod/cache/download', 'mode': 'Z'}
        },
        working_dir='/usr/src',
    )

    output = output.decode('utf-8')
    for line in output.splitlines():
        logger.info(line)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    main()

# docker run --rm -it -v `pwd`/dest/src/release-engineering-retrodep-10c3aa9:/usr/src:Z
#   -w /usr/src --network none golang:1.11 make build
