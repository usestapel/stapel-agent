"""Direct unit tests for parsing.py's JSON extraction."""
import pytest

from stapel_agent.parsing import parse_json_response, parse_translation_response


class TestParseJsonResponse:
    def test_direct_json_object(self):
        result, comment = parse_json_response('{"a": 1, "b": [2, 3]}')
        assert result == {"a": 1, "b": [2, 3]}
        assert comment is None

    def test_direct_json_array(self):
        result, comment = parse_json_response('[1, 2, {"x": "y"}]')
        assert result == [1, 2, {"x": "y"}]
        assert comment is None

    def test_json_block_with_comment(self):
        text = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
        result, comment = parse_json_response(text)
        assert result == {"a": 1}
        assert comment == "Here you go:\nHope that helps."

    def test_bare_code_block(self):
        result, comment = parse_json_response('```\n{"a": 1}\n```')
        assert result == {"a": 1}
        assert comment is None

    def test_object_anywhere(self):
        result, comment = parse_json_response('The answer is {"a": 1} as requested')
        assert result == {"a": 1}
        assert comment == "The answer is\nas requested"

    def test_array_anywhere(self):
        result, comment = parse_json_response("Values: [1, 2, 3] done")
        assert result == [1, 2, 3]
        assert comment == "Values:\ndone"

    def test_garbage_returns_none_with_comment(self):
        result, comment = parse_json_response("not json at all")
        assert result is None
        assert comment == "not json at all"

    def test_empty_string(self):
        result, comment = parse_json_response("")
        assert result is None
        assert comment is None

    def test_invalid_json_in_block_falls_through(self):
        # Block content is broken but a valid object exists elsewhere.
        text = '```json\nnot json\n```\n{"b": 2}'
        result, comment = parse_json_response(text)
        assert result == {"b": 2}
        assert comment == "```json\nnot json\n```"

    def test_leading_brace_invalid_falls_back_to_inner_object(self):
        # Starts with "{" but is not valid JSON as a whole; the greedy
        # object regex then fails too — no result.
        result, comment = parse_json_response("{broken")
        assert result is None
        assert comment == "{broken"


class TestParseTranslationResponse:
    def test_plain_object(self):
        assert parse_translation_response('{"k": "v"}') == {"k": "v"}

    def test_json_code_block(self):
        assert parse_translation_response('```json\n{"k": "v"}\n```') == {"k": "v"}

    def test_object_with_surrounding_text(self):
        assert parse_translation_response('Sure: {"k": "v"}') == {"k": "v"}

    def test_empty_string_gives_empty_dict(self):
        assert parse_translation_response("") == {}

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_translation_response("cannot translate this")

    def test_non_object_json_raises(self):
        with pytest.raises(ValueError):
            parse_translation_response('```json\n[1, 2]\n```')
