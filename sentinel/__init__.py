"""Sentinel — a miniature error-monitoring pipeline (mini-Sentry).

SDK (TypeScript) captures exceptions -> Python ingest API (/api/store) ->
stack-trace fingerprinting groups events into issues (dedupe) -> issue store ->
query + alert API + minimal dashboard.
"""

__version__ = "1.0.0"
