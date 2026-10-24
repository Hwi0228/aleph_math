import json
import queue
import threading
from pathlib import Path

from flask import Response, current_app, render_template, request

from auth import login_required
from constants import NAV_ITEMS
from ocr_service import extract_text


def ocr_page():
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    engine = request.form.get("engine", "trocr") if request.method == "POST" else request.args.get("engine", "trocr")
    if engine not in {"trocr", "easyocr"}:
        engine = "trocr"

    extracted_text = None
    error = None
    if request.method == "POST":
        uploaded = request.files.get("image")
        if uploaded is None or not uploaded.filename:
            error = "인식할 문제 이미지 파일을 선택해 주세요."
        elif Path(uploaded.filename).suffix.lower() not in allowed_extensions:
            error = "PNG, JPG, WEBP, BMP, TIFF 형식의 이미지 파일을 선택해 주세요."
        else:
            image_bytes = uploaded.read()
            if not image_bytes:
                error = "선택한 파일이 비어 있습니다."
            else:
                try:
                    correct_text = request.form.get("correct_text", "on") == "on"
                    binarize = request.form.get("binarize", "on") == "on"
                    extracted_text = extract_text(
                        image_bytes,
                        engine,
                        correct_text=correct_text,
                        binarize=binarize,
                    )
                    if not extracted_text.strip():
                        extracted_text = None
                        error = "문자를 찾지 못했습니다. 이미지가 선명한지 확인하거나 다른 OCR 모델을 선택해 보세요."
                except ValueError as exc:
                    error = str(exc)
                except Exception:
                    current_app.logger.exception("OCR inference failed using engine=%s", engine)
                    error = (
                        "OCR 실행에 실패했습니다. 필요한 패키지가 설치되어 있는지, "
                        "인터넷 연결이 가능한지 확인해 주세요. 모델의 첫 실행에는 가중치 다운로드가 필요합니다."
                    )

    return render_template(
        "ocr.html",
        nav_items=NAV_ITEMS,
        engine=engine,
        extracted_text=extracted_text,
        error=error,
    )

def ocr_stream():
    """Stream OCR progress and the final result to the browser."""
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    engine = request.form.get("engine", "trocr")
    if engine not in {"trocr", "easyocr"}:
        engine = "trocr"
    correct_text = request.form.get("correct_text", "on") == "on"
    binarize = request.form.get("binarize", "on") == "on"

    def encode_event(event):
        return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    def response_for_events(events):
        return Response(
            events,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return response_for_events([
            encode_event({"type": "error", "message": "인식할 문제 이미지 파일을 선택해 주세요."})
        ])
    if Path(uploaded.filename).suffix.lower() not in allowed_extensions:
        return response_for_events([
            encode_event({"type": "error", "message": "PNG, JPG, WEBP, BMP, TIFF 형식의 이미지 파일을 선택해 주세요."})
        ])

    image_bytes = uploaded.read()
    if not image_bytes:
        return response_for_events([
            encode_event({"type": "error", "message": "선택한 파일이 비어 있습니다."})
        ])

    events = queue.Queue()
    app_logger = current_app.logger

    def report_progress(message):
        events.put({"type": "log", "message": message})

    def run_ocr():
        try:
            extracted_text = extract_text(
                image_bytes,
                engine,
                progress_callback=report_progress,
                correct_text=correct_text,
                binarize=binarize,
            )
            if not extracted_text.strip():
                events.put({
                    "type": "error",
                    "message": (
                        "문자를 찾지 못했습니다. 이미지가 선명한지 확인하거나 "
                        "다른 OCR 모델을 선택해 보세요."
                    ),
                })
            else:
                events.put({
                    "type": "result",
                    "text": extracted_text,
                    "message": "OCR 완료: " + str(len(extracted_text)) + "자 추출",
                })
        except ValueError as exc:
            events.put({"type": "error", "message": str(exc)})
        except Exception as exc:
            app_logger.exception("OCR streaming inference failed using engine=%s", engine)
            details = str(exc).strip() or "자세한 오류 메시지가 없습니다."
            events.put({
                "type": "error",
                "message": (
                    "OCR 실행 실패 (" + type(exc).__name__ + "): " + details[:500]
                ),
            })

    def stream():
        yield encode_event({"type": "log", "message": "OCR 요청을 받았습니다."})
        worker = threading.Thread(target=run_ocr, daemon=True)
        worker.start()
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                # Keep slow first-time model downloads alive through reverse proxies.
                yield ": keep-alive\n\n"
                continue
            yield encode_event(event)
            if event.get("type") in {"result", "error"}:
                break

    return response_for_events(stream())

def register_ocr_routes(app):
    app.add_url_rule(
        "/ocr",
        endpoint="ocr_page",
        view_func=login_required(ocr_page),
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/ocr/stream",
        endpoint="ocr_stream",
        view_func=login_required(ocr_stream),
        methods=["POST"],
    )
