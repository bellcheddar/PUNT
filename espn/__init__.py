"""The ESPN data layer: fetch, cache, parse, record and replay.

Nothing above this package knows that ESPN exists. `engine/` and the routes
consume the dataclasses in `models.py` and never a raw JSON blob, which is what
makes the replay harness a drop-in and an upstream shape change a one-module fix.
"""
