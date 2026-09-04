"""The job: incremental writes, stage visibility, refresh, idempotence."""
import pytest

from stapel_agent.analysis import (
    DONE,
    ERROR_EMPTY_INPUT,
    FAILED,
    ON_PHOTOS,
    RUNNING,
    SKIPPED,
    AnalysisState,
    Fingerprint,
    JobContext,
    MemoryStateStore,
    Stage,
    StageBatch,
    runner,
)


def _fast(ctx: JobContext):
    return {"title": "Toyota Camry", "observations": {"color": "белый"}}


def _slow(ctx: JobContext):
    seen = []
    total = 3
    for i in range(total):
        seen.append({"slug": f"f{i}", "value": i})
        yield StageBatch(result=list(seen), done=i + 1, total=total)


PIPELINE = [
    Stage("text", _fast, depends_on=frozenset({ON_PHOTOS})),
    Stage("features", _slow),
]


def _start(store, fp, *, seller_filled=()):
    return runner.start(
        store=store, key="k", fingerprint=fp, stages=PIPELINE, seller_filled=seller_filled
    )


def test_stage_a_result_is_readable_while_b_still_runs():
    store = MemoryStateStore()
    seen = []

    def watching(ctx):
        for i in range(2):
            seen.append(store.load("k").stage("text").as_dict())
            yield StageBatch(result=[i], done=i + 1, total=2)

    pipeline = [Stage("text", _fast, depends_on=frozenset({ON_PHOTOS})),
                Stage("features", watching)]
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"], text="t"),
                 stages=pipeline)
    runner.run(store=store, key="k", stages=pipeline)

    assert [s["status"] for s in seen] == [DONE, DONE]
    assert seen[0]["result"]["title"] == "Toyota Camry"


def test_features_stage_writes_after_every_batch():
    store = MemoryStateStore()
    snapshots = []

    def recording(ctx):
        for i in range(3):
            yield StageBatch(result=[{"slug": f"f{i}"}], done=i + 1, total=3)
            snapshots.append(store.load("k").stage("features").as_dict())

    pipeline = [Stage("features", recording)]
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]),
                 stages=pipeline)
    runner.run(store=store, key="k", stages=pipeline)

    assert [s["progress"] for s in snapshots] == [
        {"done": 1, "total": 3}, {"done": 2, "total": 3}, {"done": 3, "total": 3}
    ]
    assert snapshots[0]["status"] == RUNNING
    assert snapshots[0]["result"] == [{"slug": "f0"}]


def test_identical_request_joins_the_job_instead_of_starting_a_second():
    store = MemoryStateStore()
    fp = Fingerprint.of(photos=[b"a"], text="hello")
    _, first = _start(store, fp)
    _, second = _start(store, fp)
    assert (first, second) == (True, False)


def test_changed_text_keeps_the_photo_stage_and_reruns_the_rest():
    store = MemoryStateStore()
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"], text="one"),
                 stages=PIPELINE)
    runner.run(store=store, key="k", stages=PIPELINE)

    calls = []

    def counted(ctx):
        calls.append("text")
        return {"title": "again"}

    pipeline = [Stage("text", counted, depends_on=frozenset({ON_PHOTOS})),
                Stage("features", _slow)]
    state, started = runner.start(
        store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"], text="two"),
        stages=pipeline,
    )
    assert started
    assert state.stage("text").status == DONE      # carried, not re-asked
    assert state.stage("features").status != DONE
    runner.run(store=store, key="k", stages=pipeline)
    assert calls == []


def test_new_photos_rerun_the_photo_stage():
    store = MemoryStateStore()
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"], text="one"),
                 stages=PIPELINE)
    runner.run(store=store, key="k", stages=PIPELINE)
    state, started = _start(store, Fingerprint.of(photos=[b"a", b"b"], text="one"))
    assert started
    assert state.stage("text").status != DONE


def test_seller_filled_slugs_ride_on_the_document():
    store = MemoryStateStore()
    seen = {}

    def peek(ctx):
        seen.update({"filled": ctx.seller_filled})
        return []

    pipeline = [Stage("features", peek)]
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]),
                 stages=pipeline, seller_filled=["color", "make_ref_select"])
    runner.run(store=store, key="k", stages=pipeline)
    assert seen["filled"] == {"color", "make_ref_select"}


def test_a_failing_blocking_stage_skips_the_rest_and_fails_the_job():
    store = MemoryStateStore()

    def boom(ctx):
        raise RuntimeError("provider down")

    pipeline = [Stage("text", boom), Stage("features", _slow)]
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]),
                 stages=pipeline)
    state = runner.run(store=store, key="k", stages=pipeline)
    assert state.status == FAILED
    assert state.stage("text").error.startswith("RuntimeError")
    assert state.stage("features").status == SKIPPED


def test_a_failing_non_blocking_stage_leaves_the_job_done():
    store = MemoryStateStore()

    def boom(ctx):
        raise RuntimeError("screening down")

    pipeline = [Stage("text", _fast), Stage("moderation", boom, blocking=False)]
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]),
                 stages=pipeline)
    state = runner.run(store=store, key="k", stages=pipeline)
    assert state.status == DONE
    assert state.stage("moderation").status == FAILED


def test_document_round_trips_through_json():
    import json

    store = MemoryStateStore()
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"], text="t"),
                 stages=PIPELINE)
    runner.run(store=store, key="k", stages=PIPELINE)
    doc = store.load("k").as_dict()
    assert json.loads(json.dumps(doc)) == doc
    assert set(doc) == {
        "status", "fingerprint", "error", "stages", "seller_filled", "updated_at",
    }
    assert set(doc["stages"]["features"]) == {"status", "result", "error", "progress"}


def test_wait_for_stage_returns_none_when_the_budget_expires():
    store = MemoryStateStore()
    runner.start(store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]),
                 stages=PIPELINE)
    assert runner.wait_for_stage(store, "k", "text", budget_seconds=0.05,
                                 poll_seconds=0.01) is None


@pytest.mark.django_db
def test_model_store_persists_the_document():
    from stapel_agent.analysis import ModelStateStore

    store = ModelStateStore()
    assert store.load("k") is None
    state = AnalysisState.fresh(Fingerprint.of(photos=[b"a"]), ["text"])
    store.save("k", state)
    store.save("k", state)  # upsert, not a duplicate row
    assert ModelStateStore().load("k").fingerprint == state.fingerprint


# ─── A job over nothing is a failure, not an answer ────────────────────────


def test_empty_input_fails_the_job_and_never_runs_a_stage():
    """The defect this exists for: `sha256("")` on both halves is not a job.

    The composer's job was started over an empty request body while the
    draft row held two photos and a title. Every stage ran, each answered
    about a listing that was not there, and the screening stage published
    "not allowed, the content is empty" — a verdict on the reader, not on
    the seller.
    """
    store = MemoryStateStore()
    ran = []
    pipeline = [
        Stage("text", lambda ctx: ran.append("text")),
        Stage("features", lambda ctx: ran.append("features")),
    ]

    state, started = runner.start(
        store=store, key="k", fingerprint=Fingerprint.of(), stages=pipeline
    )

    assert started is False  # nothing to execute
    assert state.status == FAILED
    assert state.error == ERROR_EMPTY_INPUT
    assert [state.stage(s.name).status for s in pipeline] == [SKIPPED, SKIPPED]
    assert ran == []
    assert store.load("k").error == ERROR_EMPTY_INPUT


def test_one_half_of_the_input_is_enough_to_run():
    """Photos with no words, or words with no photos, are real questions."""
    for fingerprint in (
        Fingerprint.of(photos=[b"jpeg"]),
        Fingerprint.of(text="iPhone 13 128 ГБ"),
    ):
        store = MemoryStateStore()
        state, started = _start(store, fingerprint)
        assert started is True
        assert state.error is None
        assert state.status != FAILED


def test_empty_input_error_survives_the_document_round_trip():
    state = AnalysisState.fresh(Fingerprint.of(), ["text"])
    state.status = FAILED
    state.error = ERROR_EMPTY_INPUT

    assert AnalysisState.from_dict(state.as_dict()).error == ERROR_EMPTY_INPUT
    assert AnalysisState.from_dict({}).error is None


def test_a_stage_with_nothing_to_do_is_skipped_not_run():
    store = MemoryStateStore()
    ran = []
    pipeline = [
        Stage("text", _fast, depends_on=frozenset({ON_PHOTOS})),
        Stage(
            "moderation",
            lambda ctx: ran.append("screened") or {"allowed": False},
            blocking=False,
            skip_when=lambda ctx: not (ctx.result_of("text") or {}).get("title"),
        ),
    ]
    runner.start(
        store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]), stages=pipeline
    )
    state = runner.run(store=store, key="k", stages=pipeline)
    assert state.stage("moderation").status == DONE  # there WAS a title
    assert ran == ["screened"]

    store2 = MemoryStateStore()
    pipeline[0] = Stage("text", lambda ctx: {"title": ""}, depends_on=frozenset({ON_PHOTOS}))
    runner.start(
        store=store2, key="k", fingerprint=Fingerprint.of(photos=[b"b"]), stages=pipeline
    )
    state = runner.run(store=store2, key="k", stages=pipeline)

    assert state.stage("moderation").status == SKIPPED
    assert state.stage("moderation").result is None
    assert state.status == DONE  # a skip is not a failure
    assert ran == ["screened"]  # the screener was never asked the empty question


def test_a_raising_skip_predicate_runs_the_stage_rather_than_dropping_it():
    store = MemoryStateStore()

    def boom(ctx):
        raise RuntimeError("predicate is broken")

    pipeline = [Stage("text", _fast, skip_when=boom)]
    runner.start(
        store=store, key="k", fingerprint=Fingerprint.of(photos=[b"a"]), stages=pipeline
    )
    state = runner.run(store=store, key="k", stages=pipeline)

    assert state.stage("text").status == DONE
