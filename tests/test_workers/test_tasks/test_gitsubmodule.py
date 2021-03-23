from unittest import mock

from cachito.workers.tasks import gitsubmodule

url = "https://github.com/release-engineering/retrodep.git"
ref = "c50b93a32df1c9d700e3e80996845bc2e13be848"
archive_path = f"/tmp/cachito-archives/release-engineering/retrodep/{ref}.tar.gz"


@mock.patch("git.Repo")
@mock.patch("cachito.workers.tasks.gitsubmodule.update_request_with_package")
def test_add_git_submodules_as_package(
    mock_update_with_package, mock_repo, task_passes_state_check
):
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
    # Verify that update_request_with_package was called correctly
    mock_update_with_package.assert_called_once_with(3, package, package_subpath="tour")
