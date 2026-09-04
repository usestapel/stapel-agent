"""The job's HTTP surface, as a base view a host mounts on its own route.

``POST`` starts or refreshes the job and answers with the state at once;
``GET`` returns the state. Both answer the SAME document, so a client that
polls and a client that waits read one shape.

The base is deliberately hook-shaped rather than concrete: the key of a
job, what its fingerprint is taken over, how the work is executed (inline,
a task queue, a thread) and who may ask are all facts of the host's
product, while the state machine and the wire shape are not. A host that
had to restate the state machine to change the queue would have two of
them within a release.
"""
from __future__ import annotations

from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelErrorResponse, StapelResponse

from . import runner
from .state import Fingerprint
from .store import ModelStateStore

#: HTTP 404 when the job has never been started for this subject.
ERR_404_ANALYSIS_NOT_FOUND = "error.404.analysis_not_found"


class AnalysisJobView(APIView):
    """``POST``/``GET`` one analysis job.

    A host subclasses this and implements :meth:`job_key`,
    :meth:`fingerprint_for` and :meth:`stages_for`; :meth:`execute` decides
    whether the work runs here or in a worker.
    """

    #: Which stage ``?wait=1`` waits for, and for how long. The wait covers
    #: the FAST stage only — waiting for the whole job is what the job
    #: exists to stop doing.
    fast_stage: str = ""
    fast_wait_seconds: float = 8.0

    # ── hooks ───────────────────────────────────────────────────────────
    def job_key(self, request, **kwargs) -> str:
        raise NotImplementedError

    def fingerprint_for(self, request, **kwargs) -> Fingerprint:
        raise NotImplementedError

    def stages_for(self, request, **kwargs) -> list:
        raise NotImplementedError

    def seller_filled_for(self, request, **kwargs) -> list:
        """Slugs the person filled themselves; never asked again."""
        raw = (request.data or {}).get("seller_filled") or []
        return [str(s) for s in raw if str(s).strip()]

    def get_store(self):
        return ModelStateStore()

    def execute(self, *, key, stages, inputs) -> None:
        """Run the job. Override to hand it to a queue instead of running it."""
        runner.run(store=self.get_store(), key=key, stages=stages, inputs=inputs)

    def inputs_for(self, request, **kwargs) -> dict:
        return {}

    # ── the surface ─────────────────────────────────────────────────────
    def post(self, request, **kwargs):
        store = self.get_store()
        key = self.job_key(request, **kwargs)
        stages = self.stages_for(request, **kwargs)
        state, started = runner.start(
            store=store,
            key=key,
            fingerprint=self.fingerprint_for(request, **kwargs),
            stages=stages,
            seller_filled=self.seller_filled_for(request, **kwargs),
        )
        if started:
            self.execute(
                key=key, stages=stages, inputs=self.inputs_for(request, **kwargs)
            )
        if self._wants_wait(request) and self.fast_stage:
            waited = runner.wait_for_stage(
                store, key, self.fast_stage, budget_seconds=self.fast_wait_seconds
            )
            state = waited or store.load(key) or state
        else:
            state = store.load(key) or state
        return StapelResponse(state.as_dict(), status=202)

    def get(self, request, **kwargs):
        state = self.get_store().load(self.job_key(request, **kwargs))
        if state is None:
            return StapelErrorResponse(404, ERR_404_ANALYSIS_NOT_FOUND)
        return StapelResponse(state.as_dict())

    @staticmethod
    def _wants_wait(request) -> bool:
        return str(request.query_params.get("wait") or "").lower() in ("1", "true", "yes")


__all__ = ["ERR_404_ANALYSIS_NOT_FOUND", "AnalysisJobView"]
