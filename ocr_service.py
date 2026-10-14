"""Lazy-loaded OCR backends used by the ProbOCR web application."""

from functools import lru_cache
import io
import unicodedata


MODEL_NAME = "ddobokki/ko-trocr"
ALLOWED_ENGINES = {"trocr", "easyocr"}
MAX_IMAGE_PIXELS = 20_000_000
CORRECTION_MODEL_NAME = "Qwen/Qwen3-0.6B"


@lru_cache(maxsize=1)
def _load_correction_engine():
    """Load Qwen3-0.6B for contextual OCR correction on first use."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(CORRECTION_MODEL_NAME)
    has_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if has_cuda else "cpu")
    dtype = torch.float16 if has_cuda else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        CORRECTION_MODEL_NAME,
        torch_dtype=dtype,
    )
    try:
        model.to(device)
    except RuntimeError:
        if not has_cuda:
            raise
        # TrOCR can already occupy most VRAM. Reload the small LLM on CPU if needed.
        del model
        torch.cuda.empty_cache()
        device = torch.device("cpu")
        model = AutoModelForCausalLM.from_pretrained(
            CORRECTION_MODEL_NAME,
            torch_dtype=torch.float32,
        )
        model.to(device)
    model.eval()
    return tokenizer, model, device


def _generate_corrected_text(text, tokenizer, model, device):
    """Use the full OCR output as context for Qwen3-0.6B."""
    import torch

    messages = [
        {
            "role": "system",
            "content": (
                "너는 한국어 시험지와 교재의 OCR 텍스트를 교정하는 엔진이다. "
                "입력 전체를 문맥으로 활용해 잘못 인식된 글자, 붙어 있거나 빠진 띄어쓰기, "
                "명백한 맞춤법 오류를 적극적으로 바로잡아라. 문맥상 필요한 경우 문장을 자연스럽게 "
                "다듬어도 된다. 줄바꿈과 문항 순서는 가능한 유지하고 내용을 임의로 생략하지 마라. "
                "숫자, 수식, 기호, 보기 번호는 가능한 보존하되 주변 문맥과 명백하게 충돌하는 OCR 오류는 "
                "수정해도 된다. 설명이나 인사말 없이 교정된 텍스트만 출력하라."
            ),
        },
        {
            "role": "user",
            "content": "다음은 OCR로 추출한 전체 텍스트다. 문맥을 참고해 교정해라.\n\n" + text,
        },
    ]
    model_inputs = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_dict=True,
        return_tensors="pt",
    )
    model_inputs = {
        key: value.to(device)
        for key, value in model_inputs.items()
    }
    input_length = model_inputs["input_ids"].shape[-1]
    if input_length > 28000:
        raise ValueError("OCR 텍스트가 너무 길어 한 번에 문맥 교정을 수행할 수 없습니다.")

    max_new_tokens = min(4096, max(256, input_length + 128))
    with torch.inference_mode():
        generated = model.generate(
            **model_inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated_ids = generated[0][input_length:]
    return tokenizer.decode(generated_ids, skip_special_tokens=True).strip()


def _correct_korean_text(text: str, report) -> str:
    """Correct the complete OCR result with a context-aware small language model."""
    if not text.strip():
        report("문맥 교정 대상이 없어 OCR 원문을 유지합니다.")
        return text

    report(
        f"문맥 교정 모델({CORRECTION_MODEL_NAME})을 준비합니다. "
        "첫 실행 시 약 1.5GB 모델 다운로드가 필요합니다."
    )
    try:
        tokenizer, model, device = _load_correction_engine()
    except Exception as exc:
        report(
            "문맥 교정 모델을 불러오지 못해 OCR 원문을 유지합니다. "
            f"({type(exc).__name__}: {str(exc)[:160]})"
        )
        return text

    report("Qwen3-0.6B: OCR 결과 전체를 문맥으로 사용해 교정합니다.")
    try:
        corrected = _generate_corrected_text(text, tokenizer, model, device)
    except Exception as exc:
        report(
            "문맥 교정에 실패해 OCR 원문을 유지합니다. "
            f"({type(exc).__name__}: {str(exc)[:160]})"
        )
        return text

    if not corrected:
        report("문맥 교정 결과가 비어 있어 OCR 원문을 유지합니다.")
        return text

    if corrected != text:
        old_preview = " ↵ ".join(text.splitlines())
        new_preview = " ↵ ".join(corrected.splitlines())
        if len(old_preview) > 96:
            old_preview = old_preview[:93] + "..."
        if len(new_preview) > 96:
            new_preview = new_preview[:93] + "..."
        report(f"Qwen3 교정 전: {old_preview}")
        report(f"Qwen3 교정 후: {new_preview}")
    report(f"Qwen3-0.6B 문맥 교정 완료: {len(text)}자 → {len(corrected)}자.")
    return corrected


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
    """Recognize text, with optional Qwen3 context correction and TrOCR binarization."""
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
