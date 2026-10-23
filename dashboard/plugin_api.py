"""Authenticated Hermes plugin namespace; no heavy work on the gateway event loop."""
import asyncio
import importlib.util
import sys
from pathlib import Path
from fastapi import APIRouter, Query

_NAME = '_hermes_usage_plus_bridge'
spec = importlib.util.spec_from_file_location(_NAME, Path(__file__).resolve().parents[1]/'bridge.py')
if spec is None or spec.loader is None:
    raise ImportError('Usage+ bridge missing')
_bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_bridge)
sys.modules[_NAME] = _bridge
router = APIRouter()


@router.get('/summary')
async def summary(days: int = Query(30, ge=1, le=365)):
    return await asyncio.to_thread(_bridge.fetch_snapshot, 'summary', days)


@router.get('/accounts')
async def accounts():
    return await asyncio.to_thread(_bridge.fetch_snapshot, 'accounts')


@router.get('/pricing')
async def pricing():
    return await asyncio.to_thread(_bridge.fetch_snapshot, 'pricing-status')
