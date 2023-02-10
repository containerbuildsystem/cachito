import functools
from typing import Iterable, Optional

__all__ = ["match_parent_module"]


def _contains_package(parent_name: str, package_name: str) -> bool:
    """
    Check that parent module/package contains specified package.

    :param parent_name: name of parent module or package
    :param package_name: name of package to check
    :return: True if package belongs to parent, False otherwise
    """
    if not package_name.startswith(parent_name):
        return False
    if len(package_name) > len(parent_name):
        # Check that the subpackage is {parent_name}/* and not {parent_name}*/*
        return package_name[len(parent_name)] == "/"
    # At this point package_name == parent_name, every package contains itself
    return True


def match_parent_module(package_name: str, module_names: Iterable[str]) -> Optional[str]:
    """
    Find parent module for package in iterable of module names.

    Picks the longest module name that matches the package name
    (the package name must start with the module name).

    :param package_name: name of package
    :param module_names: iterable of module names
    :return: longest matching module name or None (no module matches)
    """
    contains_this_package = functools.partial(_contains_package, package_name=package_name)
    return max(
        filter(contains_this_package, module_names),
        key=len,  # type: ignore
        default=None,
    )
