#!/bin/bash
set -e

# The first 6 commands are added here for using NSS_WRAPPER. See "Support Arbitrary User IDs" in
# https://access.redhat.com/documentation/en-us/openshift_container_platform/3.11/html/creating_images/creating-images-guidelines
export USER_ID=$(id -u)
export GROUP_ID=$(id -g)
envsubst < /src/docker/passwd.template > /tmp/passwd
export LD_PRELOAD=/usr/lib64/libnss_wrapper.so
export NSS_WRAPPER_PASSWD=/tmp/passwd
export NSS_WRAPPER_GROUP=/etc/group
exec "$@"
