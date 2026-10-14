"""Lazy-loaded OCR backends used by the ProbOCR web application."""

from functools import lru_cache
import io
import re
import unicodedata


MODEL_NAME = "ddobokki/ko-trocr"
ALLOWED_ENGINES = {"trocr", "easyocr"}
MAX_IMAGE_PIXELS = 20_000_000
CORRECTION_MODEL_NAME = "Soyoung97/gec_kr"
MATH_MARKER_RE = re.compile(r"(?:\\[A-Za-z]+|[$=<>±×÷√∑∫^_{}+*/])")
CIRCLED_PREFIX_RE = re.compile(r"^(\s*[①-⑳])\s*(.*)$")
NUMBERED_PREFIX_RE = re.compile(r"^(\s*[1-9][.)．]?)\s+(.*)$")


@lru_cache(maxsize=1)
def _load_correction_engine():
    """Load a Korean grammatical-error-correction model on first use."""
    import torch
    from transformers import BartForConditionalGeneration, PreTrainedTokenizerFast

    tokenizer = PreTrainedTokenizerFast.from_pretrained(CORRECTION_MODEL_NAME)
    model = BartForConditionalGeneration.from_pretrained(CORRECTION_MODEL_NAME)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        model.to(device)
    except RuntimeError:
        if device.type != "cuda":
            raise
        # OCR model may already occupy most GPU memory. Keep correction usable on CPU.
        torch.cuda.empty_cache()
        device = torch.device("cpu")
        model.to(device)
    model.eval()
    return tokenizer, model, device


def _split_choice_prefix(line: str):
    """Keep multiple-choice labels outside the generative correction model."""
    match = CIRCLED_PREFIX_RE.match(line)
    if match and match.group(2).strip():
        return match.group(1).strip(), match.group(2).strip()
    match = NUMBERED_PREFIX_RE.match(line)
    if match and match.group(2).strip():
        return match.group(1).strip(), match.group(2).strip()
    return "", line.strip()


def _should_correct_line(line: str) -> bool:
    """Protect equations, symbol-heavy rows, and short non-sentence labels."""
    prefix, body = _split_choice_prefix(line)
    if len(re.findall(r"[가-힣]", body)) < 4:
        return False
    if MATH_MARKER_RE.search(line):
        return False
    if not re.search(r"[가-힣]", body):
        return False
    return True


def _generate_corrected_line(text, tokenizer, model, device):
    """Run one Korean sentence through Soyoung97's Korean GEC BART model."""
    import torch

    token_ids = tokenizer.encode(text, add_special_tokens=False)
    if not token_ids:
        return text
    if len(token_ids) > 150:
        # Long lines are likely merged rows; don't truncate content to correct them.
        raise ValueError("문장이 너무 길어 보정을 건너뜁니다.")

    input_ids = []
    if tokenizer.bos_token_id is not None:
        input_ids.append(tokenizer.bos_token_id)
    input_ids.extend(token_ids)
    if tokenizer.eos_token_id is not None:
        input_ids.append(tokenizer.eos_token_id)

    tensor = torch.tensor([input_ids], dtype=torch.long, device=device)
    max_length = min(192, max(64, len(input_ids) + 32))
    with torch.inference_mode():
        generated = model.generate(
            tensor,
            max_length=max_length,
            num_beams=4,
            early_stopping=True,
            repetition_penalty=2.0,
        )
    return tokenizer.decode(generated[0], skip_special_tokens=True).strip()


def _correct_korean_text(text: str, report) -> str:
    """Correct Korean typos/grammar line-by-line while preserving layout and equations."""
    lines = text.split("\n")
    eligible = [line for line in lines if _should_correct_line(line)]
    if not eligible:
        report("한국어 문장 교정 대상이 없어 원문을 유지합니다.")
        return text

    report(
        f"한국어 맞춤법·문장 교정 모델({CORRECTION_MODEL_NAME})을 준비합니다. "
        "첫 실행 시 약 500MB 모델 다운로드가 필요합니다."
    )
    try:
        tokenizer, model, device = _load_correction_engine()
    except Exception as exc:
        report(
            "문장 교정 모델을 불러오지 못해 OCR 원문을 유지합니다. "
            f"({type(exc).__name__}: {str(exc)[:160]})"
        )
        return text

    changed = 0
    output = []
    for line_number, line in enumerate(lines, start=1):
        prefix, body = _split_choice_prefix(line)
        if not _should_correct_line(line):
            output.append(line)
            continue
        try:
            corrected = _generate_corrected_line(body, tokenizer, model, device)
        except Exception as exc:
            report(
                f"{line_number}번째 줄 교정 실패, 원문을 유지합니다. "
                f"({type(exc).__name__}: {str(exc)[:120]})"
            )
            output.append(line)
            continue

        if not corrected:
            output.append(line)
            continue
        corrected_line = f"{prefix} {corrected}".strip() if prefix else corrected
        output.append(corrected_line)
        if corrected_line != line.strip():
            changed += 1
            old_preview = line.strip()
            new_preview = corrected_line
            if len(old_preview) > 64:
                old_preview = old_preview[:61] + "..."
            if len(new_preview) > 64:
                new_preview = new_preview[:61] + "..."
            report(
                f"맞춤법·문장 보정 ({line_number}번째 줄): "
                f"{old_preview} → {new_preview}"
            )

    report(f"한국어 맞춤법·문장 보정 완료: {changed}개 줄을 수정했습니다.")
    return "\n".join(output)


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


def _segment_text_lines(image, progress_callback=None):
    """Segment text rows using horizontal projection, not disconnected word contours."""
    import cv2
    import numpy as np
    from PIL import ImageEnhance, ImageOps

    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, threshold = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # Find large photo/map-like regions on a dilated copy, then mask them before
    # row projection. Otherwise, texture inside a photograph can glue rows together.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    dilated = cv2.dilate(threshold, kernel, iterations=2)
    contours, _ = cv2.findContours(
        dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes = [cv2.boundingRect(contour) for contour in contours]
    large_visuals = [
        (x, y, width, height)
        for x, y, width, height in boxes
        if width >= image.width * 0.65
        and height >= max(120, image.height * 0.18)
    ]

    text_mask = threshold.copy()
    for x, y, width, height in large_visuals:
        text_mask[y : y + height, x : x + width] = 0

    if large_visuals and progress_callback:
        progress_callback(
            f"TrOCR: 사진·지도 등 큰 시각 영역 {len(large_visuals)}개를 "
            "줄 검출에서 제외했습니다."
        )

    # Projection groups all characters and words sharing a baseline into one
    # full-width text row. Contour boxes alone often split at large word spaces.
    row_ink = np.count_nonzero(text_mask, axis=1)
    min_row_ink = max(8, int(image.width * 0.006))
    active_rows = row_ink >= min_row_ink

    # Bridge tiny gaps caused by disconnected Hangul strokes (e.g. vowel dots).
    y = 0
    while y < len(active_rows):
        if active_rows[y]:
            y += 1
            continue
        gap_start = y
        while y < len(active_rows) and not active_rows[y]:
            y += 1
        gap_size = y - gap_start
        if gap_start > 0 and y < len(active_rows) and gap_size <= 5:
            active_rows[gap_start:y] = True

    bands = []
    band_start = None
    for y, is_active in enumerate(active_rows):
        if is_active and band_start is None:
            band_start = y
        elif not is_active and band_start is not None:
            if y - band_start >= 8:
                bands.append((band_start, y))
            band_start = None
    if band_start is not None and len(active_rows) - band_start >= 8:
        bands.append((band_start, len(active_rows)))

    crops = []
    for line_number, (top, bottom) in enumerate(bands, start=1):
        column_ink = np.count_nonzero(text_mask[top:bottom], axis=0)
        min_column_ink = max(2, int((bottom - top) * 0.02))
        columns = np.flatnonzero(column_ink >= min_column_ink)
        if not len(columns):
            continue

        left = max(0, int(columns[0]) - 12)
        right = min(image.width, int(columns[-1]) + 13)
        padded_top = max(0, top - 7)
        padded_bottom = min(image.height, bottom + 7)

        crop = image.crop((left, padded_top, right, padded_bottom))
        grayscale = ImageOps.autocontrast(crop.convert("L"))
        crop = ImageEnhance.Sharpness(grayscale).enhance(1.25).convert("RGB")
        crops.append(crop)

        if progress_callback:
            progress_callback(
                f"TrOCR: 줄 후보 {line_number} 크롭 "
                f"x={left}:{right}, y={padded_top}:{padded_bottom} "
                f"({right - left}×{padded_bottom - padded_top}px)"
            )

    if not crops:
        if progress_callback:
            progress_callback(
                "TrOCR: 수평 투영으로 텍스트 줄을 찾지 못했습니다. "
                "전체 이미지를 대체 입력으로 사용합니다."
            )
        return [image]

    return crops

def _binarize_for_trocr(image):
    """Apply Otsu binarization to a cropped scan before TrOCR inference."""
    import cv2
    import numpy as np
    from PIL import Image

    grayscale = np.asarray(image.convert("L"))
    _, threshold = cv2.threshold(
        grayscale, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    return Image.fromarray(threshold).convert("RGB")


def extract_text(
    image_bytes: bytes,
    engine: str,
    progress_callback=None,
    correct_text: bool = True,
    binarize: bool = True,
) -> str:
    """Recognize text, with optional Korean GEC correction and TrOCR binarization."""
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
        if binarize:
            report("TrOCR: OCR 입력 크롭에 Otsu 이진화를 적용합니다.")
        else:
            report("TrOCR: OCR 입력 크롭 이진화를 사용하지 않습니다.")
        report("TrOCR: 텍스트 줄을 분할합니다.")
        crops = _segment_text_lines(image, progress_callback=report)
        report(f"TrOCR: 인식할 텍스트 줄 후보 {len(crops)}개를 찾았습니다.")
        recognized_lines = []
        recognized_count = 0
        # Small batches reduce peak RAM/VRAM use on scanned worksheets.
        for start in range(0, len(crops), 8):
            batch = crops[start : start + 8]
            model_images = (
                [_binarize_for_trocr(crop) for crop in batch]
                if binarize
                else batch
            )
            pixel_values = processor(images=model_images, return_tensors="pt").pixel_values.to(device)
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
        if correct_text and result:
            result = _correct_korean_text(result, report)
        else:
            report("한국어 맞춤법·문장 보정을 사용하지 않습니다.")
        return result

    import numpy as np

    reader = loaded[1]
    if binarize:
        report("EasyOCR: 이진화 옵션은 TrOCR 전용이므로 원본 이미지를 사용합니다.")
    report("EasyOCR: 텍스트 영역을 검출하고 인식합니다.")
    detections = reader.readtext(np.asarray(image), detail=0, paragraph=False)
    lines = [
        unicodedata.normalize("NFC", str(text)).strip()
        for text in detections
        if str(text).strip()
    ]
    report(f"EasyOCR: 텍스트 영역 {len(lines)}개 인식 완료.")
    result = "\n".join(lines).strip()
    if correct_text and result:
        result = _correct_korean_text(result, report)
    else:
        report("한국어 맞춤법·문장 보정을 사용하지 않습니다.")
    return result
