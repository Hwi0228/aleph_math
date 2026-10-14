"""Lazy-loaded OCR backends used by the ProbOCR web application."""

from functools import lru_cache
import io
import unicodedata


MODEL_NAME = "ddobokki/ko-trocr"
ALLOWED_ENGINES = {"trocr", "easyocr"}
MAX_IMAGE_PIXELS = 20_000_000
@lru_cache(maxsize=1)
def _load_engine(engine: str):
    """Load only the most recently selected engine and reuse it across requests."""
    if engine == "trocr":
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        processor = TrOCRProcessor.from_pretrained(MODEL_NAME)
        model = VisionEncoderDecoderModel.from_pretrained(MODEL_NAME)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device)
        model.eval()
        return ("trocr", processor, model, device)

    if engine == "easyocr":
        import easyocr
        import torch

        reader = easyocr.Reader(["ko", "en"], gpu=torch.cuda.is_available())
        return ("easyocr", reader)

    raise ValueError("지원하지 않는 OCR 모델입니다.")


def _decode_image(image_bytes: bytes):
    """Decode untrusted uploaded image bytes with a pixel-count guard."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not image_bytes:
        raise ValueError("이미지 파일이 비어 있습니다.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            if image.width < 1 or image.height < 1:
                raise ValueError("이미지 크기가 올바르지 않습니다.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError(
                    "이미지 해상도가 너무 큽니다. 가로×세로 2,000만 픽셀 이하로 줄여 주세요."
                )
            image.load()
            return image.convert("RGB")
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("이미지를 읽을 수 없습니다. 손상되지 않은 이미지 파일인지 확인해 주세요.") from exc


def _segment_text_lines(image):
    """Return rough top-to-bottom text-line crops using connected components."""
    import cv2
    import numpy as np

    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, threshold = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    dilated = cv2.dilate(threshold, kernel, iterations=2)
    contours, _ = cv2.findContours(
        dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes = sorted(
        (cv2.boundingRect(contour) for contour in contours),
        key=lambda box: (box[1], box[0]),
    )

    crops = []
    for x, y, width, height in boxes:
        if height < 10 or width < 20:
            continue
        padding = 4
        left = max(0, x - padding)
        top = max(0, y - padding)
        right = min(image.width, x + width + padding)
        bottom = min(image.height, y + height + padding)
        crops.append(image.crop((left, top, right, bottom)))
        if len(crops) >= 160:
            break

    return crops or [image]

def extract_text(image_bytes: bytes, engine: str, progress_callback=None) -> str:
    """Recognize Korean/English text using the user-selected OCR engine."""
    if engine not in ALLOWED_ENGINES:
        raise ValueError("지원하지 않는 OCR 모델입니다. TrOCR 또는 EasyOCR를 선택해 주세요.")

    report = progress_callback or (lambda _message: None)
    engine_label = "TrOCR" if engine == "trocr" else "EasyOCR"
    report("이미지 파일 형식과 해상도를 확인합니다.")
    image = _decode_image(image_bytes)
    report(
        f"{engine_label} 모델을 준비합니다. 첫 실행은 가중치 다운로드 때문에 오래 걸릴 수 있습니다."
    )
    loaded = _load_engine(engine)
    report(f"{engine_label} 모델 준비 완료.")

    if loaded[0] == "trocr":
        import torch

        _, processor, model, device = loaded
        report("TrOCR: 텍스트 줄을 분할합니다.")
        report("TrOCR: 텍스트 줄을 분할합니다.")
        crops = _segment_text_lines(image)
        report(f"TrOCR: 인식할 텍스트 영역 {len(crops)}개를 찾았습니다.")
        report(f"TrOCR: 인식할 텍스트 줄 후보 {len(crops)}개를 찾았습니다.")
        recognized_lines = []
        recognized_count = 0
        # Small batches reduce peak RAM/VRAM use on scanned worksheets.
        for start in range(0, len(crops), 8):
            batch = crops[start : start + 8]
            pixel_values = processor(images=batch, return_tensors="pt").pixel_values.to(device)
            with torch.inference_mode():
                generated_ids = model.generate(pixel_values, max_new_tokens=160)
            decoded = processor.batch_decode(generated_ids, skip_special_tokens=True)
            for offset, line in enumerate(decoded):
                line_number = start + offset + 1
                normalized = unicodedata.normalize("NFC", line).strip()
                # Keep empty lines between detections so a missed row is visible
                # in the copied output instead of silently shifting later choices.
                recognized_lines.append(normalized)
                if normalized:
                    recognized_count += 1
                    preview = normalized if len(normalized) <= 48 else normalized[:45] + "..."
                    report(
                        f"TrOCR: 줄 {line_number}/{len(crops)} 인식 결과: {preview}"
                    )
                else:
                    report(
                        f"TrOCR: 줄 {line_number}/{len(crops)} 인식 결과가 비어 있습니다. "
                        "원본을 확인하거나 EasyOCR로 다시 시도해 주세요."
                    )
            completed = min(start + len(batch), len(crops))
            report(f"TrOCR: 텍스트 인식 진행 중 ({completed}/{len(crops)}개 줄)")
        result = "\n".join(recognized_lines).strip()
        report(f"TrOCR: 인식 완료 ({recognized_count}/{len(crops)}개 줄에서 텍스트를 얻었습니다).")
        return result

    import numpy as np

    reader = loaded[1]
    report("EasyOCR: 텍스트 영역을 검출하고 인식합니다.")
    detections = reader.readtext(np.asarray(image), detail=0, paragraph=False)
    lines = [
        unicodedata.normalize("NFC", str(text)).strip()
        for text in detections
        if str(text).strip()
    ]
    report(f"EasyOCR: 텍스트 영역 {len(lines)}개 인식 완료.")
    result = "\n".join(lines).strip()
    return result
