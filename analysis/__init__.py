"""Staged analysis as a job: a state document, a stage runner, an endpoint.

The problem it exists for: one blocking request that runs a whole chain of
model calls and answers only when the last one lands. Everything the first
call already knew is invisible until then, a slow tail holds a web worker,
and there is nothing to poll — so the caller's only options are to wait or
to lose the work.

Here the chain is a JOB on a subject. Its state is one JSON document with a
stage apiece; a fast stage's answer is readable while a slow one is still
running; the slow stage writes after every batch, so its partial answer and
its ``progress`` are readable too. A refresh re-runs only the stages whose
inputs actually changed.

The pieces:

* :mod:`~stapel_agent.analysis.state` — the document and its fingerprint.
* :mod:`~stapel_agent.analysis.store` — where the document lives.
* :mod:`~stapel_agent.analysis.runner` — start/refresh and run the stages.
* :mod:`~stapel_agent.analysis.blocks` — ordering a catalogue's features
  into the order a composer asks them, with every un-askable field carrying
  a reason instead of vanishing.
* :mod:`~stapel_agent.analysis.views` — the ``POST``/``GET`` base view.
"""

# Nothing below is imported at module level. A package body that imports its
# own submodules holds its lock while taking theirs, which is the import-lock
# deadlock 3.14 reports as `_DeadlockError` (see images/__init__.py). The
# names are resolved on first ACCESS instead — same ergonomics, no lock order.
_EXPORTS = {
    "blocks": ".blocks",
    "runner": ".runner",
    "state": ".state",
    "store": ".store",
    "views": ".views",
    "ASK_INLINE": ".blocks",
    "ASK_TEXT": ".blocks",
    "UNASKABLE": ".blocks",
    "Ask": ".blocks",
    "Block": ".blocks",
    "bounds_for": ".blocks",
    "classify": ".blocks",
    "compose_blocks": ".blocks",
    "flatten": ".blocks",
    "options": ".blocks",
    "options_ref": ".blocks",
    "resolution_order": ".blocks",
    "ON_PHOTOS": ".runner",
    "ON_TEXT": ".runner",
    "JobContext": ".runner",
    "Stage": ".runner",
    "StageBatch": ".runner",
    "run": ".runner",
    "start": ".runner",
    "wait_for_stage": ".runner",
    "DONE": ".state",
    "FAILED": ".state",
    "QUEUED": ".state",
    "RUNNING": ".state",
    "SKIPPED": ".state",
    "AnalysisState": ".state",
    "Fingerprint": ".state",
    "StageState": ".state",
    "MemoryStateStore": ".store",
    "ModelStateStore": ".store",
    "StateStore": ".store",
    "AnalysisJobView": ".views",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(module_name, __name__)
    value = module if name == module_name.lstrip(".") else getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return __all__
