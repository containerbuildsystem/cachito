from cachito.workers.pkg_managers import yarn


def test_get_npm_proxy_repo_name():
    assert yarn.get_yarn_proxy_repo_name(3) == "cachito-yarn-3"


def test_get_npm_proxy_repo_url():
    assert yarn.get_yarn_proxy_repo_url(3).endswith("/repository/cachito-yarn-3/")


def test_get_npm_proxy_username():
    assert yarn.get_yarn_proxy_repo_username(3) == "cachito-yarn-3"
