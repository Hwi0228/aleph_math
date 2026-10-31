(() => {
  const form = document.querySelector(".upload-form");
  const fileInput = form?.querySelector('input[type="file"]');
  const submitButton = form?.querySelector('button[type="submit"]');
  const feedback = document.getElementById("process-feedback");
  const steps = [
    document.getElementById("log-file"),
    document.getElementById("log-upload"),
    document.getElementById("log-extract"),
    document.getElementById("log-review")
  ];

  function setStep(index, state, message) {
    const item = steps[index];
    if (!item) return;
    item.classList.remove("active", "done", "error");
    if (state) item.classList.add(state);
    if (message) item.textContent = message;
  }

  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) {
      setStep(0, "active", "파일 선택 대기");
      if (feedback) feedback.textContent = "파일을 선택하면 처리 단계가 여기에 기록됩니다.";
      return;
    }
    const size = (file.size / 1024 / 1024).toFixed(2);
    setStep(0, "done", "파일 확인 완료 · " + file.name + " (" + size + " MB)");
    setStep(1, "", "서버 업로드 대기");
    setStep(2, "", "문항 추출 대기");
    setStep(3, "", "결과 검토 대기");
    if (feedback) feedback.textContent = "파일이 선택되었습니다. 문항 추출을 시작할 수 있습니다.";
  });

  form?.addEventListener("submit", () => {
    if (!fileInput?.files?.length) return;
    setStep(0, "done", "파일 확인 완료");
    setStep(1, "active", "파일을 서버로 전송하는 중");
    setStep(2, "", "문항 추출 대기");
    setStep(3, "", "결과 검토 대기");
    if (feedback) feedback.textContent = "서버에서 문서를 분석하고 있습니다. 요청이 끝나면 결과 또는 오류가 표시됩니다.";
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "문서 분석 중…";
    }
  });

  const downloadButton = document.getElementById("download-json");
  const jsonOutput = document.getElementById("json-result");
  downloadButton?.addEventListener("click", () => {
    if (!jsonOutput) return;
    const blob = new Blob([jsonOutput.value], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "questions.json";
    link.click();
    URL.revokeObjectURL(url);
  });
})();
