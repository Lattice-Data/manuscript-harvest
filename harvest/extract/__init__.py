"""Extraction stage: corpus bytes -> blocks of text with provenance."""

__version__ = "0.1.0"

from .blocks import Block, read_blocks, write_blocks  # noqa: F401
from .limits import Limits  # noqa: F401
