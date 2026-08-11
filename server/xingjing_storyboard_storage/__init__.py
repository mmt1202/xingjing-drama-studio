"""M05 storyboard object-storage adapters."""

from .local import LocalStoryboardObjectStorage, ObjectNotFound, StoredObject

__all__ = ["LocalStoryboardObjectStorage", "ObjectNotFound", "StoredObject"]
