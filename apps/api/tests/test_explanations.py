import json
import os
import unittest
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.database import Agent, Alert, Case, ExplanationCall
from app.services.explanations import _prompt, generate_case_explanation


class ExplanationGenerationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        agent = Agent(code="EXP", display_name="Explanation test", area="Dhaka")
        self.session.add(agent); self.session.commit(); self.session.refresh(agent)
        self.alert = Alert(
            agent_id=agent.id, provider="bkash", severity="high", priority_score=70,
            title="Provider pressure", summary="Balance may deplete soon", confidence=.82,
            reasons_json=json.dumps(["Recent outflow is above baseline"]),
            evidence_json=json.dumps([{"source": "forecast", "rule": "rate_projection", "text": "20 minutes remaining"}]),
            recommended_actions_json=json.dumps([{"key": "notify_ops", "label": "Notify Operations", "weight": .97}]),
            fused_explanation="High pressure detected; human review required.",
            owner_role="ops", owner_label="Operations", initial_owner="liquidity",
        )
        self.session.add(self.alert); self.session.commit(); self.session.refresh(self.alert)
        self.case = Case(alert_id=self.alert.id, owner_role="ops", owner_label="Operations")
        self.session.add(self.case); self.session.commit(); self.session.refresh(self.case)

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def test_missing_key_uses_transparent_evidence_fallback(self):
        with patch.dict(os.environ, {"EXPLANATION_PROVIDER": "gemini"}, clear=True):
            generate_case_explanation(self.session, self.case, self.alert)
        payload = json.loads(self.case.explanation_json)
        self.assertEqual(self.case.explanation_status, "fallback")
        self.assertEqual(payload["source"], "deterministic_fallback")
        self.assertIn("Recent outflow", payload["factors"][0])
        self.assertIn("Human review", payload["disclaimer"])

    def test_gemini_structured_output_is_validated_and_cannot_add_actions(self):
        vendor_response = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "summary": "Current evidence indicates unusual service pressure that needs review.",
                "factors": ["The supplied projection reports about 20 minutes remaining."],
                "uncertainty": "Demand changes could alter the projection.",
                "recommended_next_step": "Ask Operations to review the current provider position.",
            })}]}}]
        }
        with patch.dict(os.environ, {"EXPLANATION_PROVIDER": "gemini", "GEMINI_API_KEY": "test-key"}, clear=True), patch(
            "app.services.explanations._post_json", return_value=vendor_response
        ):
            generate_case_explanation(self.session, self.case, self.alert)
        payload = json.loads(self.case.explanation_json)
        self.assertEqual(self.case.explanation_status, "generated")
        self.assertEqual(self.case.explanation_provider, "gemini")
        self.assertEqual(payload["safe_recommendations"], ["Notify Operations"])
        self.assertEqual(payload["source"], "llm")

    def test_unsafe_vendor_wording_falls_back(self):
        vendor_response = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "summary": "Block account immediately.", "factors": ["A score exists"], "uncertainty": "None",
                "recommended_next_step": "Request human review",
            })}]}}]
        }
        with patch.dict(os.environ, {"EXPLANATION_PROVIDER": "gemini", "GEMINI_API_KEY": "test-key"}, clear=True), patch(
            "app.services.explanations._post_json", return_value=vendor_response
        ):
            generate_case_explanation(self.session, self.case, self.alert)
        self.assertEqual(self.case.explanation_status, "fallback")
        self.assertIn("advisory-language", self.case.explanation_error)

    def test_groq_structured_output_path(self):
        vendor_response = {"choices": [{"message": {"content": json.dumps({
            "summary": "The supplied evidence indicates provider pressure requiring review.",
            "factors": ["The rate projection reports a short remaining buffer."],
            "uncertainty": "Demand may change after the observation window.",
            "recommended_next_step": "Ask Operations to review the current provider position.",
        })}}]}
        with patch.dict(os.environ, {"EXPLANATION_PROVIDER": "groq", "GROQ_API_KEY": "test-key"}, clear=True), patch(
            "app.services.explanations._get_json", return_value={"data": [{"id": "openai/gpt-oss-20b"}]}
        ), patch("app.services.explanations._GROQ_MODEL_CACHE", None), patch(
            "app.services.explanations._post_json", return_value=vendor_response
        ) as post:
            generate_case_explanation(self.session, self.case, self.alert)
        self.assertEqual(self.case.explanation_provider, "groq")
        self.assertEqual(self.case.explanation_status, "generated")
        self.assertIn("api.groq.com", post.call_args.args[0])
        self.assertEqual(post.call_args.args[2]["response_format"]["json_schema"]["strict"], True)

    def test_requested_language_and_alert_data_are_in_the_vendor_prompt(self):
        vendor_response = {"choices": [{"message": {"content": json.dumps({
            "summary": "বাংলায় নির্দিষ্ট সতর্কতার ব্যাখ্যা।",
            "factors": ["২০ মিনিটের প্রক্ষেপিত সময়"],
            "uncertainty": "চাহিদা পরিবর্তিত হতে পারে।",
            "recommended_next_step": "অপারেশনস টিমকে মানব পর্যালোচনা করতে বলুন।",
        })}}]}
        with patch.dict(os.environ, {"EXPLANATION_PROVIDER": "groq", "GROQ_API_KEY": "test-key"}, clear=True), patch(
            "app.services.explanations._get_json", return_value={"data": [{"id": "openai/gpt-oss-20b"}]}
        ), patch("app.services.explanations._GROQ_MODEL_CACHE", None), patch(
            "app.services.explanations._post_json", return_value=vendor_response
        ) as post:
            generate_case_explanation(self.session, self.case, self.alert, language="bn")
        body = post.call_args.args[2]
        prompt = body["messages"][0]["content"]
        self.assertIn("Bengali", prompt)
        self.assertIn("bkash", prompt.lower())
        self.assertIn("20 minutes remaining", prompt)
        self.assertEqual(self.case.explanation_language, "bn")
        call = self.session.exec(select(ExplanationCall).where(ExplanationCall.case_id == self.case.id)).one()
        self.assertEqual(call.language, "bn")
        self.assertEqual(call.status, "generated")
        self.assertNotIn("test-key", call.request_json)

    def test_different_situations_produce_different_data_driven_prompts(self):
        second = Alert(
            agent_id=self.alert.agent_id, provider="nagad", severity="medium", priority_score=52,
            title="Later provider pressure", summary="Projected buffer lasts until tomorrow morning",
            confidence=.61, reasons_json=json.dumps(["Evening demand is above its recent baseline"]),
            evidence_json=json.dumps([
                {"source": "forecast", "rule": "rate_projection", "text": "9 hours remaining"},
            ]),
            recommended_actions_json=json.dumps([
                {"key": "request_confirmation", "label": "Request agent confirmation", "weight": .8},
            ]),
            fused_explanation="Unusual pressure requires review.", owner_role="ops",
            owner_label="Operations", initial_owner="liquidity",
        )
        first_prompt = _prompt(self.alert, "en")
        second_prompt = _prompt(second, "en")

        self.assertNotEqual(first_prompt, second_prompt)
        self.assertIn("bkash", first_prompt.lower())
        self.assertIn("20 minutes remaining", first_prompt)
        self.assertIn("nagad", second_prompt.lower())
        self.assertIn("9 hours remaining", second_prompt)

    def test_bengali_uses_same_pipeline_and_changed_data_changes_prompt(self):
        changed = Alert(
            agent_id=self.alert.agent_id, provider="rocket", severity="high", priority_score=78,
            title="Provider pressure", summary="Projected shortage is later in the day",
            confidence=.73, reasons_json=json.dumps(["Afternoon service demand is elevated"]),
            evidence_json=json.dumps([
                {"source": "forecast", "rule": "rate_projection", "text": "4 hours remaining"},
            ]),
            recommended_actions_json=json.dumps([
                {"key": "notify_ops", "label": "Notify Operations", "weight": .92},
            ]),
            fused_explanation="Unusual pressure requires review.", owner_role="ops",
            owner_label="Operations", initial_owner="liquidity",
        )

        original_prompt = _prompt(self.alert, "bn")
        changed_prompt = _prompt(changed, "bn")

        self.assertNotEqual(original_prompt, changed_prompt)
        self.assertIn("Bengali", original_prompt)
        self.assertIn("20 minutes remaining", original_prompt)
        self.assertIn("rocket", changed_prompt.lower())
        self.assertIn("4 hours remaining", changed_prompt)


if __name__ == "__main__":
    unittest.main()
