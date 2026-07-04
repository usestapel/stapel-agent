"""Image seam unit tests — ImageRef matrix, GeneratedImage, helpers.
Django-free module, no db."""
import base64

import pytest

from stapel_agent.images.base import (
    GeneratedImage,
    ImageGenProvider,
    ImageRef,
    RetryableImageGenError,
    b64_decoded_size,
)

PNG = b"\x89PNG\r\n\x1a\nfake"
PNG_B64 = base64.b64encode(PNG).decode()


class TestImageRefValidation:
    def test_url_only_is_valid(self):
        ref = ImageRef(url="https://cdn.test/a.png")
        assert ref.kind == "url"
        assert ref.mime is None  # vendor sniffs URL content types

    def test_data_only_is_valid_with_default_mime(self):
        ref = ImageRef(data=PNG)
        assert ref.kind == "data"
        assert ref.mime == "image/png"

    def test_data_with_explicit_mime(self):
        assert ImageRef(data=PNG, mime="image/webp").mime == "image/webp"

    def test_none_of_the_two_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one of url/data"):
            ImageRef()

    def test_both_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one of url/data"):
            ImageRef(url="https://x/a.png", data=PNG)

    def test_as_base64_roundtrips(self):
        ref = ImageRef(data=PNG)
        assert base64.b64decode(ref.as_base64()) == PNG


class TestImageRefFromPayload:
    def test_url_payload(self):
        ref = ImageRef.from_payload({"url": "https://x/a.png"})
        assert ref.kind == "url"

    def test_data_b64_payload_decodes(self):
        ref = ImageRef.from_payload({"data_b64": PNG_B64, "mime": "image/webp"})
        assert ref.data == PNG
        assert ref.mime == "image/webp"

    def test_data_b64_default_mime(self):
        assert ImageRef.from_payload({"data_b64": PNG_B64}).mime == "image/png"

    def test_invalid_base64_is_value_error(self):
        with pytest.raises(ValueError):
            ImageRef.from_payload({"data_b64": "not@valid base64!!"})

    def test_empty_payload_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one of url/data"):
            ImageRef.from_payload({})

    def test_in_process_raw_bytes_key(self):
        assert ImageRef.from_payload({"data": PNG}).data == PNG


class TestGeneratedImage:
    def test_to_dict_drops_absent_keys(self):
        assert GeneratedImage(data_b64=PNG_B64).to_dict() == {
            "data_b64": PNG_B64,
            "mime": "image/png",
        }
        assert GeneratedImage(url="https://x/a.png", mime="image/jpeg").to_dict() == {
            "url": "https://x/a.png",
            "mime": "image/jpeg",
        }

    def test_b64_decoded_size_exact(self):
        for payload in (b"", b"a", b"ab", b"abc", b"abcd", PNG):
            encoded = base64.b64encode(payload).decode()
            assert b64_decoded_size(encoded) == len(payload)
        assert b64_decoded_size(None) == 0

    def test_error_taxonomy(self):
        exc = RetryableImageGenError("slow down", provider="p", status_code=429)
        assert exc.provider == "p"
        assert exc.status_code == 429

    def test_abc_generate_is_abstract(self):
        from stapel_agent.tests.fakes import FakeImageProvider

        with pytest.raises(NotImplementedError):
            ImageGenProvider.generate(FakeImageProvider(), prompt="x")
