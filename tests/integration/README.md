# Integration tests for Cachito

This directory stores the integration tests for Cachito.

## Configuration

Input data for tests should be configured in the file `test_env_vars.yaml`. The file should be
placed in top-level directory of this repository. The path can be changed by setting
`CACHITO_TEST_CONFIG` to a different path.

See `test_env_vars.yaml` for a complete list of configuration options and examples at the top-level
directory of this repo.

## Running the tests

Tests can be triggered from the top-level directory of this repository with:

    tox -e integration

The integration environment is not part of the default `tox` envlist.

`REQUESTS_CA_BUNDLE` can be passed in `tox.ini` for the `integration`
environment in order to enable running the tests against Cachito instances which
have certificates issued by a custom root certificate authority. Example usage:

    REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt tox -e integration

`KRB5CCNAME` can be passed in `tox.ini` for the `integration`
environment when using Kerberos authentication in requests.

To use certificate authentication, set `api_auth_type` to `cert` in the integration tests
yaml configuration file. You must also set the environment variables `CACHITO_TEST_CERT`
and `CACHITO_TEST_KEY` to reference the certificate and key files respectively.
