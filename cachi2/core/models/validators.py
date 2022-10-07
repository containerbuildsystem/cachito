from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")


def unique(items: Iterable[T], by: Callable[[T], Any], dedupe: bool = True) -> list[T]:
    """Make sure input items are unique by the specified key.

    The 'by' function must return a hashable key (the uniqueness key).

    If item A and item B have the same key, then
        if dedupe is true (the default) and A == B, B is discarded
        if dedupe is false or A != B, raise an error
    """
    by_key = {}
    for item in items:
        key = by(item)
        if key not in by_key:
            by_key[key] = item
        elif not dedupe or by_key[key] != item:
            raise ValueError(f"conflict by {key}: {by_key[key]} X {item}")
    return list(by_key.values())


def unique_sorted(items: Iterable[T], by: Callable[[T], Any], dedupe: bool = True) -> list[T]:
    """Make sure input items are unique and sort them.

    Same as 'unique()' but the key returned from the 'by' function must support ordering.
    """
    unique_items = unique(items, by, dedupe)
    unique_items.sort(key=by)
    return unique_items
