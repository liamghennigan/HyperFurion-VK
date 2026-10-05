"""Pause punctuation: the rules, the reviewer's reply, and the [llm] call."""

from unittest import mock

import pytest

from voice_keyboard.flow import pauses
from voice_keyboard.flow.pauses import PauseDecision
from voice_keyboard.llm import LLMClient, llm_ready
from voice_keyboard.transcript import _reconcile_utterance


class TestTokens:
    @pytest.mark.parametrize("token", ["project.", "it's.", "25.", "café."])
    def test_a_plain_word_with_a_period_is_reviewable(self, token) -> None:
        assert pauses.reviewable(token)

    @pytest.mark.parametrize("token", ["etc.", "Dr.", "U.S.", "3.5.", "...", "why?", "word"])
    def test_abbreviations_and_other_endings_are_not(self, token) -> None:
        assert not pauses.reviewable(token)

    @pytest.mark.parametrize("token", ["And", "For", "Let's"])
    def test_a_sentence_start_capital_can_go(self, token) -> None:
        assert pauses.lowerable(token)

    @pytest.mark.parametrize("token", ["I", "I'm", "API", "McKinsey", "and"])
    def test_other_capitals_stay(self, token) -> None:
        assert not pauses.lowerable(token)

    def test_apply_joins_and_lowercases(self) -> None:
        decision = PauseDecision("", True, "and")
        assert pauses.apply("project.", "And", decision) == ("project", "and")

    def test_apply_comma(self) -> None:
        decision = PauseDecision(",", True, "but")
        assert pauses.apply("today.", "But", decision) == ("today,", "but")

    def test_a_changed_next_word_keeps_its_capital(self) -> None:
        decision = PauseDecision("", True, "and")
        assert pauses.apply("project.", "Andrew", decision) == ("project", "Andrew")

    def test_a_sentence_end_never_lowercases(self) -> None:
        decision = PauseDecision("?", True, "we")
        assert pauses.apply("ready.", "We", decision) == ("ready?", "We")


class TestRules:
    def test_a_conjunction_after_the_pause_continues_the_sentence(self) -> None:
        decision, confident = pauses.rule_decision("project.", "And")
        assert confident and decision == PauseDecision("", True, "and")

    def test_but_gets_a_comma(self) -> None:
        decision, confident = pauses.rule_decision("today.", "But")
        assert confident and decision == PauseDecision(",", True, "but")

    def test_a_word_no_sentence_ends_on_joins(self) -> None:
        decision, confident = pauses.rule_decision("the.", "Project")
        assert confident and decision.punct == ""
        # Unknown words keep their capital: it could be a name.
        assert not decision.lower_next

    def test_common_words_and_words_seen_lowercase_lose_the_capital(self) -> None:
        assert pauses.rule_decision("I.", "Think")[0].lower_next
        seen = frozenset({"setup"})
        assert pauses.rule_decision("the.", "Setup", lower_seen=seen)[0].lower_next

    def test_a_name_after_to_keeps_its_capital(self) -> None:
        decision, confident = pauses.rule_decision("to.", "Sarah")
        assert confident and decision == PauseDecision("", False, "sarah")

    def test_unclear_pauses_keep_the_period_unconfidently(self) -> None:
        decision, confident = pauses.rule_decision("think.", "We")
        assert not confident and decision == PauseDecision(".", False, "we")

    def test_spoken_punctuation_replaces_the_period(self) -> None:
        decision, confident = pauses.rule_decision("it.", "Comma.", right_kind="punct")
        assert confident and decision.punct == ""

    @pytest.mark.parametrize("kind", ["scratch", "break", "instruction"])
    def test_commands_after_a_pause_keep_it_a_sentence_end(self, kind) -> None:
        decision, confident = pauses.rule_decision("email.", "Scratch", right_kind=kind)
        assert confident and decision.punct == "."


class TestReviewerAnswer:
    @pytest.mark.parametrize(
        ("answer", "left", "right", "expected"),
        [
            ("project and", "project.", "And", PauseDecision("", True, "and")),
            ("today, but", "today.", "But", PauseDecision(",", True, "but")),
            ("Friday. Let's", "Friday.", "Let's", PauseDecision(".", False, "let's")),
            ("to Sarah", "to.", "Sarah", PauseDecision("", False, "sarah")),
            ('"think we"', "think.", "We", PauseDecision("", True, "we")),
            ("ready? We", "ready.", "We", PauseDecision("?", False, "we")),
            # A chatty reply still holds the pair.
            ("reply: the project and how", "project.", "And", PauseDecision("", True, "and")),
        ],
    )
    def test_clean_replies_are_read(self, answer, left, right, expected) -> None:
        assert pauses.parse_answer(answer, left, right) == expected

    @pytest.mark.parametrize(
        "answer", ["", "project", "project... and", "project — and", "proj and", "and project"]
    )
    def test_anything_else_is_rejected(self, answer) -> None:
        assert pauses.parse_answer(answer, "project.", "And") is None


class TestUtteranceReconcile:
    def test_an_utterance_that_repeats_its_chunks_adds_nothing(self) -> None:
        spoken = "So I was thinking. And how we could."
        assert _reconcile_utterance(spoken, "So I was thinking. And how we could.") == spoken

    def test_words_beyond_the_chunks_are_appended(self) -> None:
        assert _reconcile_utterance("So I was", "So I was thinking.") == "So I was thinking."

    def test_a_disagreeing_utterance_never_rewrites_what_was_received(self) -> None:
        assert _reconcile_utterance("eye scream", "ice cream") == "eye scream"

    def test_with_no_chunks_the_utterance_is_taken(self) -> None:
        assert _reconcile_utterance("", "Hello there.") == "Hello there."
        assert _reconcile_utterance("Hello.", "") == "Hello."


class TestReviewCall:
    def _response(self, content: str) -> mock.Mock:
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {"choices": [{"message": {"content": content}}]}
        return response

    def test_the_review_sends_both_sides_and_returns_the_reply(self) -> None:
        client = LLMClient(base_url="https://api.x.ai/v1", api_key="k", model="m")
        with mock.patch(
            "voice_keyboard.llm.requests.post", return_value=self._response("project and")
        ) as post:
            reply = client.review_pause("about the project.", "And how we")
        assert reply == "project and"
        payload = post.call_args.kwargs["json"]
        assert payload["temperature"] == 0.0 and payload["max_tokens"] == 24
        assert payload["messages"][1]["content"] == (
            "before: about the project.\nafter: And how we\nreply:"
        )
        assert post.call_args.kwargs["timeout"] <= 5.0

    def test_ready_needs_a_real_key_unless_local(self) -> None:
        assert llm_ready({"providers": {"xai": {"api_key": "xai-real"}}})
        assert not llm_ready({"providers": {"xai": {"api_key": "xai-your-api-key-here"}}})
        assert not llm_ready({})
        assert llm_ready({"llm": {"provider": "openai", "base_url": "http://127.0.0.1:8080/v1"}})
        # A hyperfurion key is not an xAI key: no reviewer.
        assert not llm_ready({"providers": {"hyperfurion": {"api_key": "hfk-x"}}})
