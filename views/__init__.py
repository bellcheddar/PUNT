"""Blueprints. One module per surface, registered by the factory in `app.py`.

`tabs` renders pages, `partials` renders the htmx fragments those pages poll,
`api` is JSON, `media` proxies and serves binary assets, and `admin` is the
env-gated commissioner surface. The split is by *response kind* rather than by
tab, because caching, error handling and degradation differ per kind and not
per tab: a broken partial swaps in a stale banner, a broken page renders an
empty state, and a broken API call returns a 200 with `problems` populated.
"""
