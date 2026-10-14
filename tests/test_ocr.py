import io
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import ocr_service

from PIL import Image

import app as app_module


class OCRRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        database = Path(self.temp_directory.name) / "ocr-tests.sqlite3"
        app_module.app.config.update(
            TESTING=True,
            DATABASE=str(database),
            SECRET_KEY="test-session-secret",
        )
        self.client = app_module.app.test_client()
        self.client.get("/signup")
        with self.client.session_transaction() as user_session:
            token = user_session["csrf_token"]
        response = self.client.post(
            "/signup",
            data={
                "_csrf_token": token,
                "email": "ocr@example.com",
                "display_name": "OCR 테스트",
                "password": "long-test-password",
                "password_confirm": "long-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        self.temp_directory.cleanup()

    def csrf_token(self):
        # Signup clears the session; a GET creates a fresh CSRF token.
        self.client.get("/")
        with self.client.session_transaction() as user_session:
            return user_session["csrf_token"]

    def image_upload(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (16, 16), color="white").save(image_bytes, format="PNG")
        image_bytes.seek(0)
        return image_bytes

    def test_ocr_page_is_reachable_and_model_choices_are_shown(self):
        response = self.client.get("/ocr")
        self.assertEqual(response.status_code, 200)
        self.assertIn("TrOCR".encode(), response.data)
        self.assertIn("EasyOCR".encode(), response.data)

    def test_ocr_post_passes_selected_engine_and_image_to_backend(self):
        with patch("app.extract_text", return_value="인식된 문제 내용") as mocked_ocr:
            response = self.client.post(
                "/ocr",
                data={
                    "_csrf_token": self.csrf_token(),
                    "engine": "easyocr",
                    "image": (self.image_upload(), "sample.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("인식된 문제 내용".encode(), response.data)
        mocked_ocr.assert_called_once()
        self.assertEqual(mocked_ocr.call_args.args[1], "easyocr")

    def test_ocr_stream_sends_progress_logs_and_result(self):
        def fake_extract(_image_bytes, _engine, progress_callback=None, restore_spacing=True):
            progress_callback("테스트용 모델 준비 완료")
            progress_callback("TrOCR: 텍스트 인식 진행 중 (1/1개 영역)")
            return "첫 번째 항목 1 + 2"

        with patch("app.extract_text", side_effect=fake_extract):
            response = self.client.post(
                "/ocr/stream",
                data={
                    "_csrf_token": self.csrf_token(),
                    "engine": "trocr",
                    "image": (self.image_upload(), "sample.png"),
                },
                content_type="multipart/form-data",
                buffered=True,
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("테스트용 모델 준비 완료", body)
        self.assertIn("TrOCR: 텍스트 인식 진행 중", body)
        self.assertIn("첫 번째 항목 1 + 2", body)
        self.assertIn('"type": "result"', body)

    def test_segment_text_lines_keeps_separated_words_on_one_row(self):
        from PIL import ImageDraw, ImageFont

        image = Image.new("RGB", (800, 300), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        draw.text((15, 15), "LEFT WORD", fill="black", font=font)
        draw.text((500, 15), "RIGHT WORD", fill="black", font=font)
        draw.rectangle((10, 75, 790, 215), fill=(40, 40, 40))
        draw.text((15, 240), "BOTTOM ROW", fill="black", font=font)

        logs = []
        crops = ocr_service._segment_text_lines(image, progress_callback=logs.append)

        # Two text lines remain; the wide graphic-like block is not an OCR row.
        self.assertEqual(len(crops), 2)
        self.assertGreater(crops[0].width, 500)
        self.assertLess(crops[0].height, 70)
        self.assertLess(crops[1].height, 70)
        self.assertTrue(any("크롭" in message for message in logs))
        self.assertTrue(any("큰 시각 영역" in message for message in logs))

    def test_trocr_preserves_internal_spaces(self):
        torch_stub = ModuleType("torch")
        torch_stub.inference_mode = nullcontext

        processor = Mock()
        processed = Mock()
        pixel_values = Mock()
        pixel_values.to.return_value = pixel_values
        processed.pixel_values = pixel_values
        processor.return_value = processed
        processor.batch_decode.return_value = ["공백 을 포함한 OCR 결과"]
        model = Mock()
        model.generate.return_value = object()
        loaded_engine = ("trocr", processor, model, "cpu")

        with (
            patch.dict(sys.modules, {"torch": torch_stub}),
            patch("ocr_service._decode_image", return_value=object()),
            patch("ocr_service._load_engine", return_value=loaded_engine),
            patch("ocr_service._segment_text_lines", return_value=[object()]),
        ):
            result = ocr_service.extract_text(
                b"image-bytes", "trocr", restore_spacing=False
            )

        self.assertEqual(result, "공백 을 포함한 OCR 결과")

    def test_korean_spacing_restoration_keeps_math_lines_unchanged(self):
        class FakeSpacer:
            def __init__(self):
                self.seen = []

            def space(self, line, reset_whitespace=False):
                self.seen.append((line, reset_whitespace))
                if line.startswith("다음사진과지도"):
                    return "다음 사진과 지도를 보고 이 지역의 지역성을 바르게 추론한 것은?"
                return "① 계절풍의 영향으로 벼농사가 발달하였다."

        spacer = FakeSpacer()
        logs = []
        text = (
            "다음사진과지도롤보고이지역지역성을바르게추란것은?\n"
            "따라서 x^2 = 3이다\n"
            "①계절풍의영향으로벼농사가발달하였다."
        )

        with patch("ocr_service._load_spacing_engine", return_value=spacer):
            restored = ocr_service._restore_korean_spacing(text, logs.append)

        self.assertEqual(
            restored,
            "다음 사진과 지도를 보고 이 지역의 지역성을 바르게 추론한 것은?\n"
            "따라서 x^2 = 3이다\n"
            "① 계절풍의 영향으로 벼농사가 발달하였다.",
        )
        self.assertEqual(len(spacer.seen), 2)
        self.assertTrue(all(reset for _, reset in spacer.seen))
        self.assertTrue(any("띄어쓰기 보정 완료" in message for message in logs))

    def test_trocr_logs_and_preserves_unrecognized_rows(self):
        torch_stub = ModuleType("torch")
        torch_stub.inference_mode = nullcontext

        processor = Mock()
        processed = Mock()
        pixel_values = Mock()
        pixel_values.to.return_value = pixel_values
        processed.pixel_values = pixel_values
        processor.return_value = processed
        processor.batch_decode.return_value = ["제목", "", "③ 다음 선택지"]
        model = Mock()
        model.generate.return_value = object()
        loaded_engine = ("trocr", processor, model, "cpu")
        logs = []

        with (
            patch.dict(sys.modules, {"torch": torch_stub}),
            patch("ocr_service._decode_image", return_value=object()),
            patch("ocr_service._load_engine", return_value=loaded_engine),
            patch("ocr_service._segment_text_lines", return_value=[object(), object(), object()]),
        ):
            result = ocr_service.extract_text(
                b"image-bytes",
                "trocr",
                progress_callback=logs.append,
                restore_spacing=False,
            )

        self.assertEqual(result, "제목\n\n③ 다음 선택지")
        self.assertTrue(any("인식 결과가 비어 있습니다" in message for message in logs))
        self.assertTrue(any("③ 다음 선택지" in message for message in logs))

    def test_ocr_requires_an_image(self):
        response = self.client.post(
            "/ocr",
            data={"_csrf_token": self.csrf_token(), "engine": "trocr"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("이미지 파일을 선택".encode(), response.data)

    def test_ocr_page_requires_login(self):
        anonymous = app_module.app.test_client()
        response = anonymous.get("/ocr")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
