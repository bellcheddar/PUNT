"""The engine: polls in, Moments and numbers out.

Nothing in here knows about Flask, ESPN's payload shapes, or how anything is
rendered. It takes the dataclasses from `espn.models` and produces the figures
and events the app is actually about: what the optimal lineup would have scored,
how much was left on the bench, who is doomed, and which of those facts deserves
a horn.
"""
