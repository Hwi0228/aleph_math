import csv
import os
import time
import warnings
import unicodedata
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

# 모델 생성 시 발생하는 UserWarning(max_length 관련) 숨기기
warnings.filterwarnings("ignore", category=UserWarning)


def normalize_text(text, normalize_quotes=True):
    # 1. 유니코드 NFC 정규화 (NFD 자소 분리 방지)
    t = unicodedata.normalize('NFC', text)
    
    # 2. 굽은 따옴표(‘ ’ “ ”) 및 유사 기호를 직선 따옴표(' ")로 통일
    if normalize_quotes:
        quote_map = {
            '‘': "'", '’': "'", '`': "'", '´': "'",
            '“': '"', '”': '"'
        }
        for q, rep in quote_map.items():
            t = t.replace(q, rep)
            
    return t.strip()


def calculate_metrics(ref_text, hyp_text, normalize_quotes=True):
    r = normalize_text(ref_text, normalize_quotes)
    h = normalize_text(hyp_text, normalize_quotes)

    dp = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        dp[i][0] = i
    for j in range(len(h) + 1):
        dp[0][j] = j

    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            if r[i - 1] == h[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    edit_distance = dp[len(r)][len(h)]
    ref_len = len(r) if len(r) > 0 else 1

    cer = (edit_distance / ref_len) * 100
    accuracy = max(0.0, 100.0 - cer)
    return edit_distance, accuracy

def run_batch_evaluation(
    gt_file="ground_truth.txt",
    image_dir="cropped_lines",
    output_csv="evaluation_results_nfc.csv",
):
    if not os.path.exists(gt_file):
        print(f"에러: 원문 파일({gt_file})이 존재하지 않습니다.")
        return

    # 원문 읽어오기
    with open(gt_file, "r", encoding="utf-8") as f:
        gt_lines = [line.strip() for line in f if line.strip()]

    # 모델 세팅
    model_name = "ddobokki/ko-trocr"
    print(f"TrOCR 모델 로딩 중 ({model_name})...")
    processor = TrOCRProcessor.from_pretrained(model_name)
    model = VisionEncoderDecoderModel.from_pretrained(model_name)

    print(f"\n총 {len(gt_lines)}개 줄에 대해 배치 평가를 시작합니다.")
    print("(※ 유니코드 NFC 정규화 적용 및 공백 포함 비교 기준)")
    print("=" * 85)
    print(f"{'번호':^5} | {'이미지 파일':<15} | {'추론시간':^8} | {'일치율':^8} | {'상태':^6}")
    print("=" * 85)

    results = []
    total_acc = 0.0
    total_time = 0.0
    valid_count = 0

    for idx, gt_text in enumerate(gt_lines, start=1):
        img_name = f"line_{idx:02d}.png"
        img_path = os.path.join(image_dir, img_name)

        if not os.path.exists(img_path):
            print(f"[{idx:02d}] 이미지 없음: {img_path}")
            continue

        image = Image.open(img_path).convert("RGB")
        
        # 추론 시작 (시간 측정)
        start_t = time.perf_counter()

        pixel_values = processor(images=image, return_tensors="pt").pixel_values  # type: ignore[call-arg]
        
        # max_length=None 을 추가하여 길이 경고 방지
        generated_ids = model.generate(
            pixel_values, 
            max_new_tokens=60, 
            max_length=None
        )  # type: ignore
        
        ocr_result = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

        infer_time = time.perf_counter() - start_t

        # 공백을 보존한 상태에서 NFC 정규화 일치율 산출
        errors, acc = calculate_metrics(gt_text, ocr_result)

        total_acc += acc
        total_time += infer_time
        valid_count += 1

        status = "PERFECT" if errors == 0 else f"{errors}자 오차"
        print(f"[{idx:02d}]   | {img_name:<15} | {infer_time:.3f}초   | {acc:6.1f}%  | {status}")

        # CSV 파일에 저장 시 보기 좋게 NFC 처리된 결과물로 기록
        ocr_result_nfc = unicodedata.normalize('NFC', ocr_result)

        results.append(
            {
                "index": idx,
                "image_path": img_path,
                "ground_truth": gt_text,
                "ocr_result_nfc": ocr_result_nfc,
                "errors": errors,
                "accuracy": round(acc, 2),
                "infer_time_sec": round(infer_time, 4),
            }
        )

    # 평가 내역 CSV 저장
    if results:
        with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    # 종합 통계 출력
    print("=" * 85)
    if valid_count > 0:
        avg_acc = total_acc / valid_count
        avg_time = total_time / valid_count
        print(f"[배치 평가 최종 결과 리포트]")
        print(f"• 전체 비교 검증 개수 : {valid_count} / {len(gt_lines)} 줄")
        print(f"• 평균 글자 일치율     : {avg_acc:.2f}%")
        print(f"• 평균 장당 추론 속도   : {avg_time:.3f}초")
        print(f"• 세부 비교 파일 저장   : {os.path.abspath(output_csv)}")
    print("=" * 85)


if __name__ == "__main__":
    run_batch_evaluation()