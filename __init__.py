"""Usage+ registers observational hooks only; no tools or prompt changes."""
from .telemetry import record_call


def register(ctx):
    # Hermes mounts API routes once. Refresh only our bridge on plugin reload,
    # preserving the route's module reference without restarting the gateway.
    import importlib.util
    import sys
    from pathlib import Path
    name = '_hermes_usage_plus_bridge'
    existing = sys.modules.get(name)
    if existing is not None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('bridge.py'))
        if spec is not None and spec.loader is not None:
            spec.loader.exec_module(existing)
    ctx.register_hook('post_api_request', record_call)
    ctx.register_hook('post_auxiliary_call', record_call)
