"""Aria channels — external entrypoints as structured-task producers."""

from .registry import (
    CHANNEL_TASK_SCHEMA,
    ChannelSpec,
    channel_map,
    default_channels,
    enabled_channels,
)

__all__ = [
    "CHANNEL_TASK_SCHEMA",
    "ChannelSpec",
    "channel_map",
    "default_channels",
    "enabled_channels",
]
