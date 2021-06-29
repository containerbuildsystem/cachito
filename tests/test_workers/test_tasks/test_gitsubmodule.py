import json
from cachito.workers.paths import RequestBundleDir
from unittest import mock

from cachito.workers.tasks import gitsubmodule

url = "https://github.com/release-engineering/retrodep.git"
ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
archive_path = f"/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz"


@mock.patch("git.Repo")
@mock.patch("cachito.workers.paths.get_worker_config")
def test_add_git_submodules_as_package(
    get_worker_config, mock_repo, task_passes_state_check, tmpdir
):
    get_worker_config.return_value = mock.Mock(cachito_bundles_dir=tmpdir)
    submodule = mock.Mock()
    submodule.name = "tour"
    submodule.hexsha = "522fb816eec295ad58bc488c74b2b46748d471b2"
    submodule.url = "https://github.com/user/tour.git"
    submodule.path = "tour"
    mock_repo.return_value.submodules = [submodule]
    package = {
        "type": "git-submodule",
        "name": "tour",
        "version": "https://github.com/user/tour.git#522fb816eec295ad58bc488c74b2b46748d471b2",
    }
    gitsubmodule.add_git_submodules_as_package(3)

    bundle_dir = RequestBundleDir(3)
    expected = package.copy()
    expected["path"] = "tour"
    expected["dependencies"] = []
    assert {"packages": [expected]} == json.loads(
        bundle_dir.git_submodule_packages_data.read_bytes()
    )
