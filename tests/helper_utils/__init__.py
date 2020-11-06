import filecmp
import os
from pathlib import Path


def assert_directories_equal(dir_a, dir_b):
    """
    Check recursively directories have equal content.

    :param dir_a: first directory to check
    :param dir_b: second directory to check
    """
    dirs_cmp = filecmp.dircmp(dir_a, dir_b)
    assert (
        len(dirs_cmp.left_only) == 0
    ), f"Found files: {dirs_cmp.left_only} in {dir_a}, but not {dir_b}."
    assert (
        len(dirs_cmp.right_only) == 0
    ), f"Found files: {dirs_cmp.right_only} in {dir_b}, but not {dir_a}."
    assert (
        len(dirs_cmp.funny_files) == 0
    ), f"Found files: {dirs_cmp.funny_files} in {dir_a}, {dir_b} which could not be compared."
    (_, mismatch, errors) = filecmp.cmpfiles(dir_a, dir_b, dirs_cmp.common_files, shallow=False)
    assert len(mismatch) == 0, f"Found mismatches: {mismatch} between {dir_a} {dir_b}."
    assert len(errors) == 0, f"Found errors: {errors} between {dir_a} {dir_b}."

    for common_dir in dirs_cmp.common_dirs:
        inner_a = os.path.join(dir_a, common_dir)
        inner_b = os.path.join(dir_b, common_dir)
        assert_directories_equal(inner_a, inner_b)


def write_file_tree(tree_def, rooted_at):
    """
    Write a file tree to disk.

    :param dict tree_def: Definition of file tree, see usage for intuitive examples
    :param (str | Path) rooted_at: Root of file tree, must be an existing directory
    """
    root = Path(rooted_at)
    for entry, value in tree_def.items():
        entry_path = root / entry
        if isinstance(value, str):
            entry_path.write_text(value)
        else:
            entry_path.mkdir()
            write_file_tree(value, entry_path)
