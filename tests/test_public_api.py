"""
Tests for the package-level public API (PEP 562 lazy exports).
"""
import os
import subprocess
import sys

import stapel_agent


class TestLazyExports:
    def test_all_declares_public_api(self):
        assert stapel_agent.__all__ == [
            "AudioRef",
            "CachePolicy",
            "LlmProvider",
            "NormalizedTranscript",
            "ProviderResult",
            "SttProvider",
            "agent_settings",
            "complete",
            "register_provider",
            "register_stt_provider",
            "registered_providers",
            "registered_stt_providers",
            "summarize",
            "transcribe",
            "translate",
        ]

    def test_agent_settings_resolves(self):
        from stapel_agent.conf import agent_settings

        assert stapel_agent.agent_settings is agent_settings

    def test_services_resolve(self):
        from stapel_agent.services import complete, translate

        assert stapel_agent.complete is complete
        assert stapel_agent.translate is translate
        assert callable(stapel_agent.complete)
        assert callable(stapel_agent.translate)

    def test_provider_seam_resolves(self):
        from stapel_agent.providers import register_provider, registered_providers
        from stapel_agent.providers.base import LlmProvider, ProviderResult

        assert stapel_agent.LlmProvider is LlmProvider
        assert stapel_agent.ProviderResult is ProviderResult
        assert stapel_agent.register_provider is register_provider
        assert stapel_agent.registered_providers is registered_providers

    def test_cache_seam_resolves(self):
        from stapel_agent.cache import CachePolicy

        assert stapel_agent.CachePolicy is CachePolicy

    def test_stt_seam_resolves(self):
        from stapel_agent.services import summarize, transcribe
        from stapel_agent.stt import register_stt_provider, registered_stt_providers
        from stapel_agent.stt.base import AudioRef, NormalizedTranscript, SttProvider

        assert stapel_agent.SttProvider is SttProvider
        assert stapel_agent.AudioRef is AudioRef
        assert stapel_agent.NormalizedTranscript is NormalizedTranscript
        assert stapel_agent.register_stt_provider is register_stt_provider
        assert stapel_agent.registered_stt_providers is registered_stt_providers
        assert stapel_agent.transcribe is transcribe
        assert stapel_agent.summarize is summarize

    def test_dir_includes_exports(self):
        listing = dir(stapel_agent)
        for name in stapel_agent.__all__:
            assert name in listing

    def test_unknown_attribute_raises(self):
        try:
            stapel_agent.nonexistent_export
        except AttributeError as exc:
            assert "nonexistent_export" in str(exc)
        else:
            raise AssertionError("expected AttributeError")


class TestImportWithoutDjangoSettings:
    def test_package_import_is_django_free(self):
        """`import stapel_agent` must not import Django nor require settings."""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        code = (
            "import sys\n"
            "import stapel_agent\n"
            'polluted = [m for m in sys.modules if m == "django" or m.startswith("django.")]\n'
            'assert not polluted, f"django imported at package import time: {polluted}"\n'
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(sys.executable),
        )
        assert result.returncode == 0, result.stderr

    def test_provider_base_import_is_django_free(self):
        """The provider/STT seams must be importable without Django too."""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        code = (
            "import sys\n"
            "from stapel_agent import LlmProvider, ProviderResult\n"
            "from stapel_agent import SttProvider, AudioRef, NormalizedTranscript\n"
            'polluted = [m for m in sys.modules if m == "django" or m.startswith("django.")]\n'
            'assert not polluted, f"django imported: {polluted}"\n'
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(sys.executable),
        )
        assert result.returncode == 0, result.stderr
