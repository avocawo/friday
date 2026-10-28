"""Standalone preview stays closed; browser checks use scoped ephemeral previews."""
from .errors import FridayError


def start(root, key):
    raise FridayError('isolation_unavailable', 'Standalone previews are unavailable; home-stretch creates an isolated preview for browser checks.')
