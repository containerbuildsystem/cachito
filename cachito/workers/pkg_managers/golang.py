# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime
import functools
import logging
import os
import os.path
import re
import shutil
import tempfile

import git
import semver

from cachito.errors import CachitoError
from cachito.workers.config import get_worker_config
from cachito.workers.paths import RequestBundleDir
from cachito.workers.pkg_managers.general import run_cmd

__all__ = ["get_golang_version", "resolve_gomod"]

log = logging.getLogger(__name__)
run_gomod_cmd = functools.partial(run_cmd, exc_msg="Processing gomod dependencies failed")


class GoCacheTemporaryDirectory(tempfile.TemporaryDirectory):
    """
    A wrapper around the TemporaryDirectory context manager to also run `go clean -modcache`.

    The files in the Go cache are read-only by default and cause the default clean up behavior of
    tempfile.TemporaryDirectory to fail with a permission error. A way around this is to run
    `go clean -modcache` before the default clean up behavior is run.
    """

    def __exit__(self, exc, value, tb):
        """
        Clean up temporary directory by first cleaning up the Go cache.
        """
        try:
            env = {"GOPATH": self.name, "GOCACHE": self.name}
            run_gomod_cmd(("go", "clean", "-modcache"), {"env": env})
        finally:
            super().__exit__(exc, value, tb)


def resolve_gomod(app_source_path, request, dep_replacements=None):
    """
    Resolve and fetch gomod dependencies for given app source archive.

    :param str app_source_path: the full path to the application source code
    :param dict request: the Cachito request this is for
    :param list dep_replacements: dependency replacements with the keys "name" and "version"; this
        results in a series of `go mod edit -replace` commands
    :return: a tuple of the Go module itself and the list of dictionaries representing the
        dependencies
    :rtype: (dict, list)
    :raises CachitoError: if fetching dependencies fails
    """
    if not dep_replacements:
        dep_replacements = []

    worker_config = get_worker_config()
    with GoCacheTemporaryDirectory(prefix="cachito-") as temp_dir:
        env = {
            "GOPATH": temp_dir,
            "GO111MODULE": "on",
            "GOCACHE": temp_dir,
            "GOPROXY": worker_config.cachito_athens_url,
            "PATH": os.environ.get("PATH", ""),
        }

        run_params = {"env": env, "cwd": app_source_path}

        # Collect all the dependency names that are being replaced to later verify if they were
        # all used
        replaced_dep_names = set()
        for dep_replacement in dep_replacements:
            name = dep_replacement["name"]
            replaced_dep_names.add(name)
            new_name = dep_replacement.get("new_name", name)
            version = dep_replacement["version"]
            log.info("Applying the gomod replacement %s => %s@%s", name, new_name, version)
            run_gomod_cmd(
                ("go", "mod", "edit", "-replace", f"{name}={new_name}@{version}"), run_params
            )

        log.info("Downloading the gomod dependencies")
        run_gomod_cmd(("go", "mod", "download"), run_params)
        go_list_output = run_gomod_cmd(
            ("go", "list", "-m", "-f", "{{.Path}} {{.Version}} {{.Replace}}", "all"), run_params
        )

        deps = []
        module_name = None
        # Keep track of which dependency replacements were actually applied to verify they were all
        # used later
        used_replaced_dep_names = set()
        go_module_name_error = "The Go module name could not be determined"
        for line in go_list_output.splitlines():
            # If there is no "replace" directive used on the dependency, then the last column will
            # be "<nil>"
            parts = [part for part in line.split(" ") if part not in ("", "<nil>")]
            if len(parts) == 1:
                # This is the application itself, not a dependency
                if module_name is not None:
                    log.error(
                        'go list produced two lines which look like module names: "%s" and "%s"',
                        module_name,
                        parts[0],
                    )
                    raise CachitoError(go_module_name_error)
                module_name = parts[0]
                continue

            replaces = None
            if len(parts) == 3:
                # If a Go module uses a "replace" directive to a local path, it will be shown as:
                # k8s.io/metrics v0.0.0 ./staging/src/k8s.io/metrics
                # In this case, just take the left side.
                parts = parts[0:2]
            elif len(parts) == 4:
                # If a Go module uses a "replace" directive, then it will be in the format:
                # github.com/pkg/errors v0.8.0 github.com/pkg/errors v0.8.1
                # In this case, just take the right side since that is the actual
                # dependency being used
                old_name, old_version = parts[0], parts[1]
                # Only keep track of user provided replaces. There could be existing "replace"
                # directives in the go.mod file, but they are an implementation detail specific to
                # Go and they don't need to be recorded in Cachito.
                if old_name in replaced_dep_names:
                    used_replaced_dep_names.add(old_name)
                    replaces = {"type": "gomod", "name": old_name, "version": old_version}
                parts = parts[2:]

            if len(parts) == 2:
                deps.append(
                    {"name": parts[0], "replaces": replaces, "type": "gomod", "version": parts[1]}
                )
            else:
                log.warning("Unexpected go module output: %s", line)

        unused_dep_replacements = replaced_dep_names - used_replaced_dep_names
        if unused_dep_replacements:
            raise CachitoError(
                "The following gomod dependency replacements don't apply: "
                f'{", ".join(unused_dep_replacements)}'
            )

        if not module_name:
            # This should never occur, but it's here as a precaution
            raise CachitoError(go_module_name_error)

        module_version = get_golang_version(
            module_name, app_source_path, request["ref"], update_tags=True
        )
        module = {"name": module_name, "type": "gomod", "version": module_version}

        bundle_dir = RequestBundleDir(request["id"])

        # Add the gomod cache to the bundle the user will later download
        tmp_download_cache_dir = os.path.join(temp_dir, RequestBundleDir.go_mod_cache_download_part)
        if not os.path.exists(tmp_download_cache_dir):
            os.makedirs(tmp_download_cache_dir, exist_ok=True)

        log.debug(
            "Adding dependencies from %s to %s",
            tmp_download_cache_dir,
            bundle_dir.gomod_download_dir,
        )
        shutil.copytree(tmp_download_cache_dir, str(bundle_dir.gomod_download_dir))

        return module, deps


def _get_golang_pseudo_version(commit, tag=None, module_major_version=None):
    """
    Get the Go module's pseudo-version when a non-version commit is used.

    For a description of the algorithm, see https://tip.golang.org/cmd/go/#hdr-Pseudo_versions.

    :param git.Commit commit: the commit object of the Go module
    :param git.Tag tag: the highest semantic version tag with a matching major version before the
        input commit. If this isn't specified, it is assumed there was no previous valid tag.
    :param int module_major_version: the Go module's major version as stated in its go.mod file. If
        this and "tag" are not provided, 0 is assumed.
    :return: the Go module's pseudo-version as returned by `go list`
    :rtype: str
    """
    # Use this instead of commit.committed_datetime so that the datetime object is UTC
    committed_dt = datetime.utcfromtimestamp(commit.committed_date)
    commit_timestamp = committed_dt.strftime(r"%Y%m%d%H%M%S")
    commit_hash = commit.hexsha[0:12]

    # vX.0.0-yyyymmddhhmmss-abcdefabcdef is used when there is no earlier versioned commit with an
    # appropriate major version before the target commit
    if tag is None:
        # If the major version isn't in the import path and there is not a versioned commit with the
        # version of 1, the major version defaults to 0.
        return f'v{module_major_version or "0"}.0.0-{commit_timestamp}-{commit_hash}'

    tag_semantic_version = semver.parse_version_info(tag.name[1:])
    # An example of a semantic version with a prerelease is v2.2.0-alpha
    if tag_semantic_version.prerelease:
        # vX.Y.Z-pre.0.yyyymmddhhmmss-abcdefabcdef is used when the most recent versioned commit
        # before the target commit is vX.Y.Z-pre
        version_seperator = "."
        pseudo_semantic_version = tag_semantic_version
    else:
        # vX.Y.(Z+1)-0.yyyymmddhhmmss-abcdefabcdef is used when the most recent versioned commit
        # before the target commit is vX.Y.Z
        version_seperator = "-"
        pseudo_semantic_version = semver.bump_patch(str(tag_semantic_version))

    return f"v{pseudo_semantic_version}{version_seperator}0.{commit_timestamp}-{commit_hash}"


def _get_highest_semver_tag(repo, target_commit, major_version, all_reachable=False):
    """
    Get the highest semantic version tag related to the input commit.

    :param Git.Repo repo: the Git repository object to search
    :param int major_version: the major version of the Go module as in the go.mod file to use as a
        filter for major version tags
    :param bool all_reachable: if False, the search is constrained to the input commit. If True,
        then the search is constrained to the input commit and preceding commits.
    :return: the highest semantic version tag if one is found
    :rtype: git.Tag
    """
    try:
        g = git.Git(repo.working_dir)
        if all_reachable:
            # Get all the tags on the input commit and all that precede it.
            # This is based on:
            # https://github.com/golang/go/blob/0ac8739ad5394c3fe0420cf53232954fefb2418f/src/cmd/go/internal/modfetch/codehost/git.go#L659-L695
            cmd = [
                "git",
                "for-each-ref",
                "--format",
                "%(refname:lstrip=-1)",
                "refs/tags",
                "--merged",
                target_commit.hexsha,
            ]
        else:
            # Get the tags that point to this commit
            cmd = ["git", "tag", "--points-at", target_commit.hexsha]

        tag_names = g.execute(cmd).splitlines()
    except git.GitCommandError:
        msg = f"Failed to get the tags associated with the reference {target_commit.hexsha}"
        log.exception(msg)
        raise CachitoError(msg)

    not_semver_tag_msg = "%s is not a semantic version tag"
    highest = None
    for tag_name in tag_names:
        if not tag_name.startswith("v"):
            log.debug(not_semver_tag_msg, tag_name)
            continue

        try:
            # Exclude the 'v' prefix since this is required by Go, but it is seen as invalid by
            # the semver Python package
            parsed_version = semver.parse_version_info(tag_name[1:])
        except ValueError:
            log.debug(not_semver_tag_msg, tag_name)
            continue

        # If the major version of the semantic version tag doesn't match the Go module's major
        # version, then ignore it
        if parsed_version.major != major_version:
            continue

        if highest is None:
            highest = tag_name
        else:
            highest_version = semver.parse_version_info(highest[1:])
            if parsed_version > highest_version:
                highest = tag_name

    if highest:
        return repo.tags[highest]

    return None


def get_golang_version(module_name, git_path, commit_sha, update_tags=False):
    """
    Get the version of the Go module in the input Git repository in the same format as `go list`.

    If commit doesn't point to a commit with a semantically versioned tag, a pseudo-version
    will be returned.

    :param str module_name: the Go module's name
    :param str git_path: the path to the Git repository
    :param str commit_sha: the Git commit SHA1 of the Go module to get the version for
    :param bool update_tags: determines if `git fetch --tags --force` should be run before
        determining the version. If this fails, it will be logged as a warning.
    :return: a version as `go list` would provide
    :rtype: str
    """
    # If the module is version v2 or higher, the major version of the module is included as /vN at
    # the end of the module path. If the module is version v0 or v1, the major version is omitted
    # from the module path.
    module_major_version = None
    match = re.match(r"(?:.+/v)(?P<major_version>\d+)$", module_name)
    if match:
        module_major_version = int(match.groupdict()["major_version"])

    repo = git.Repo(git_path)
    if update_tags:
        try:
            repo.remote().fetch(force=True, tags=True)
        except:  # noqa E722
            log.warning("Failed to fetch the tags on the Git repository for %s", module_name)

    if module_major_version:
        major_versions_to_try = (module_major_version,)
    else:
        # Prefer v1.x.x tags but fallback to v0.x.x tags if both are present
        major_versions_to_try = (1, 0)

    commit = repo.commit(commit_sha)
    for major_version in major_versions_to_try:
        # Get the highest semantic version tag on the commit with a matching major version
        tag_on_commit = _get_highest_semver_tag(repo, commit, major_version)
        if not tag_on_commit:
            continue

        log.debug(
            "Using the semantic version tag of %s for commit %s", tag_on_commit.name, commit_sha
        )
        return tag_on_commit.name

    log.debug("No semantic version tag was found on the commit %s", commit_sha)

    # This logic is based on:
    # https://github.com/golang/go/blob/a23f9afd9899160b525dbc10d01045d9a3f072a0/src/cmd/go/internal/modfetch/coderepo.go#L511-L521
    for major_version in major_versions_to_try:
        # Get the highest semantic version tag before the commit with a matching major version
        pseudo_base_tag = _get_highest_semver_tag(repo, commit, major_version, all_reachable=True)
        if not pseudo_base_tag:
            continue

        log.debug(
            "Using the semantic version tag of %s as the pseudo-base for the commit %s",
            pseudo_base_tag.name,
            commit_sha,
        )
        pseudo_version = _get_golang_pseudo_version(commit, pseudo_base_tag, major_version)
        log.debug("Using the pseudo-version %s for the commit %s", pseudo_version, commit_sha)
        return pseudo_version

    log.debug("No valid semantic version tag was found")
    # Fall-back to a vX.0.0-yyyymmddhhmmss-abcdefabcdef pseudo-version
    return _get_golang_pseudo_version(commit, module_major_version=module_major_version)
