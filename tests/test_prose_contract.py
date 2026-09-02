"""The register a generated text is written in, and the revision that fixes it.

A constrained decoder enforces a SHAPE. It says nothing about REGISTER —
whether the string in the ``description`` field is the document the product
asked for or a chat turn about it. Both validate; only one is publishable.

The defect this file was written from, observed on a live client stand: a
marketplace listing composer asked for a sale description and got a caption
of the photograph («on the photo you can see three cameras») that closed by
offering to keep talking («if you need any more details from the photo, I'll
tell you»). Every one of those answers passed its pydantic model, because
every one of them was a well-formed string in the right field.

So the check has to be a property of the TEXT, declared by the caller who
knows what register the field is for, and the library's job is to run it at
the same boundary where it already validates the shape — and, when it fails,
to say so to the model and ask again rather than handing the caller prose it
has already established is wrong.
"""
import json

import pytest
from pydantic import BaseModel, ConfigDict

from stapel_agent import services
from stapel_agent.providers.base import ProviderError, ProviderResult
from stapel_agent.safety.prose import ProseContract, check_prose


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str


# The live defect, verbatim from the stand (listing 459), plus the shape of a
# clean answer for contrast.
CAPTION_ANSWER = json.dumps(
    {
        "title": "Apple iPhone (Pro) silver — 3 cameras",
        "description": (
            "Selling an Apple iPhone Pro in silver. On the photo — the version "
            "with three main cameras.\n\nWrite to me — I'll answer questions "
            "and take more photos on request."
        ),
    }
)
CLEAN_ANSWER = json.dumps(
    {
        "title": "Apple iPhone Pro, silver",
        "description": (
            "Apple iPhone Pro in silver. Used, no visible damage to the body. "
            "A transparent case is included."
        ),
    }
)

LISTING_CONTRACT = ProseContract(
    max_chars=400,
    banned_phrases=("on the photo", "in the photograph"),
    reject_trailing_question=True,
    banned_endings=(r"I'll answer questions",),
)


class TestCheckProse:
    """The checker itself: a pure function over a string and a contract."""

    def test_a_clean_description_has_no_violations(self):
        assert check_prose("A bicycle in good condition. Two keys included.", LISTING_CONTRACT) == ()

    def test_the_photo_caption_is_caught(self):
        violations = check_prose(
            "On the photo — the version with three main cameras.", LISTING_CONTRACT
        )
        assert "banned_phrase:on the photo" in violations

    def test_the_offer_to_keep_talking_is_caught(self):
        violations = check_prose(
            "A phone in silver. Write to me — I'll answer questions", LISTING_CONTRACT
        )
        assert "banned_ending:I'll answer questions" in violations

    def test_an_ending_only_counts_at_the_END(self):
        """The phrase mid-text is not the defect; closing on it is."""
        violations = check_prose(
            "I'll answer questions is a phrase. The bicycle is red and unused.",
            LISTING_CONTRACT,
        )
        assert not any(v.startswith("banned_ending") for v in violations)

    def test_a_trailing_question_is_caught(self):
        assert "trailing_question" in check_prose("A red bicycle. Want more?", LISTING_CONTRACT)

    def test_the_length_cap_is_enforced(self):
        violations = check_prose("x" * 401, LISTING_CONTRACT)
        assert "too_long" in violations

    def test_the_cap_counts_CHARACTERS_not_bytes(self):
        """A Cyrillic title is not two thirds as long as a Latin one."""
        contract = ProseContract(max_chars=10)
        assert check_prose("двенадцать", contract) == ()  # 10 characters
        assert "too_long" in check_prose("двенадцать!", contract)

    def test_matching_ignores_case_and_yo(self):
        """«ё»/«е» is one letter to a person typing and to a model writing."""
        contract = ProseContract(banned_phrases=("на фото",))
        assert "banned_phrase:на фото" in check_prose("НА ФОТО видно", contract)

    def test_an_empty_contract_rejects_nothing(self):
        assert check_prose("anything at all?", ProseContract()) == ()


@pytest.mark.django_db
class TestCompleteJsonRevises:
    """The retry: the violations go BACK to the model, once."""

    def _validator(self, result):
        return check_prose(result.description, LISTING_CONTRACT)

    def test_a_rejected_answer_is_re_asked_and_the_second_one_is_kept(
        self, fake_provider
    ):
        answers = [ProviderResult(text=CAPTION_ANSWER), ProviderResult(text=CLEAN_ANSWER)]

        def _next(**kwargs):
            return answers.pop(0)

        fake_provider.responder = _next
        result = services.complete_json(
            "draft it",
            "small",
            schema=Draft,
            validate=self._validator,
            max_revisions=1,
        )
        assert result["status"] == "ok"
        assert "On the photo" not in result["result"].description
        # Two calls: the rejected one and the revision.
        assert len(fake_provider.calls) == 2

    def test_the_revision_prompt_TELLS_the_model_what_was_wrong(self, fake_provider):
        answers = [ProviderResult(text=CAPTION_ANSWER), ProviderResult(text=CLEAN_ANSWER)]
        fake_provider.responder = lambda **kw: answers.pop(0)
        services.complete_json(
            "draft it", "small", schema=Draft, validate=self._validator, max_revisions=1
        )
        revision_prompt = fake_provider.calls[1]["prompt"]
        assert "on the photo" in revision_prompt.lower()
        # and it still carries the original instruction
        assert "draft it" in revision_prompt

    def test_still_wrong_after_the_retry_is_a_FAILURE_not_a_shrug(self, fake_provider):
        """The caller must never receive prose the library knows is wrong."""
        fake_provider.result = ProviderResult(text=CAPTION_ANSWER)
        result = services.complete_json(
            "draft it", "small", schema=Draft, validate=self._validator, max_revisions=1
        )
        assert result["status"] == "failure"
        assert result["reason"] == services.REASON_OUTPUT_REJECTED
        assert "banned_phrase:on the photo" in result["violations"]
        assert len(fake_provider.calls) == 2  # one original + one revision, no more

    def test_max_revisions_zero_fails_on_the_first_bad_answer(self, fake_provider):
        fake_provider.result = ProviderResult(text=CAPTION_ANSWER)
        result = services.complete_json(
            "draft it", "small", schema=Draft, validate=self._validator, max_revisions=0
        )
        assert result["status"] == "failure"
        assert result["reason"] == services.REASON_OUTPUT_REJECTED
        assert len(fake_provider.calls) == 1

    def test_a_clean_first_answer_never_costs_a_second_call(self, fake_provider):
        fake_provider.result = ProviderResult(text=CLEAN_ANSWER)
        result = services.complete_json(
            "draft it", "small", schema=Draft, validate=self._validator, max_revisions=1
        )
        assert result["status"] == "ok"
        assert len(fake_provider.calls) == 1

    def test_no_validator_means_no_behaviour_change(self, fake_provider):
        """Every existing caller keeps the exact call it had."""
        fake_provider.result = ProviderResult(text=CAPTION_ANSWER)
        result = services.complete_json("draft it", "small", schema=Draft)
        assert result["status"] == "ok"
        assert len(fake_provider.calls) == 1

    def test_a_provider_failure_on_the_revision_is_reported_as_such(
        self, fake_provider
    ):
        """A revision that could not be asked is a provider failure, not a
        rejection — the caller degrades differently for the two."""
        answers = [ProviderResult(text=CAPTION_ANSWER)]

        def _next(**kwargs):
            if answers:
                return answers.pop(0)
            raise ProviderError("provider exploded")

        fake_provider.responder = _next
        result = services.complete_json(
            "draft it", "small", schema=Draft, validate=self._validator, max_revisions=1
        )
        assert result["status"] == "failure"
        assert result["reason"] != services.REASON_OUTPUT_REJECTED

    def test_the_validator_sees_the_TYPED_result(self, fake_provider):
        """It runs after pydantic, so a caller writes `result.description`,
        not `result["description"]`."""
        seen = []
        fake_provider.result = ProviderResult(text=CLEAN_ANSWER)
        services.complete_json(
            "draft it",
            "small",
            schema=Draft,
            validate=lambda r: seen.append(type(r)) or (),
            max_revisions=1,
        )
        assert seen == [Draft]
