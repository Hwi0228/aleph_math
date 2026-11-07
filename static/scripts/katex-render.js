(() => {
  "use strict";

  const version = "0.16.22";
  const cdnBases = [
    "https://cdn.jsdelivr.net/npm/katex@" + version + "/dist",
    "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/" + version,
    "https://unpkg.com/katex@" + version + "/dist"
  ];
  const delimiters = [
    { left: "$$", right: "$$", display: true },
    { left: "\\[", right: "\\]", display: true },
    { left: "\\(", right: "\\)", display: false },
    { left: "$", right: "$", display: false }
  ];

  let loadingPromise = null;
  let mathReady = false;
  let observer = null;

  function loadStylesheetFrom(baseUrl) {
    return new Promise((resolve, reject) => {
      const href = baseUrl + "/katex.min.css";
      const existing = document.querySelector(
        'link[data-hexora-katex-style="' + href + '"]'
      );
      if (existing) {
        if (existing.dataset.loaded === "true") {
          resolve(href);
          return;
        }
        existing.remove();
      }

      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.crossOrigin = "anonymous";
      link.dataset.hexoraKatexStyle = href;
      link.onload = () => {
        link.dataset.loaded = "true";
        resolve(href);
      };
      link.onerror = () => {
        link.remove();
        reject(new Error("KaTeX stylesheet failed: " + href));
      };
      document.head.appendChild(link);
    });
  }

  async function loadStylesheet() {
    const errors = [];
    for (const baseUrl of cdnBases) {
      try {
        return await loadStylesheetFrom(baseUrl);
      } catch (error) {
        errors.push(error.message);
        console.warn(error.message);
      }
    }
    throw new Error("KaTeX CSS could not be loaded from any CDN. " + errors.join(" | "));
  }

  function loadScript(url) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(
        'script[data-hexora-katex-script="' + url + '"]'
      );
      if (existing) {
        if (existing.dataset.loaded === "true") {
          resolve(url);
          return;
        }
        existing.remove();
      }

      const script = document.createElement("script");
      script.src = url;
      script.async = true;
      script.crossOrigin = "anonymous";
      script.dataset.hexoraKatexScript = url;
      script.onload = () => {
        script.dataset.loaded = "true";
        resolve(url);
      };
      script.onerror = () => {
        script.remove();
        reject(new Error("KaTeX script failed: " + url));
      };
      document.head.appendChild(script);
    });
  }

  async function loadEngine() {
    const errors = [];

    for (const baseUrl of cdnBases) {
      try {
        if (!window.katex) {
          await loadScript(baseUrl + "/katex.min.js");
        }
        if (typeof window.renderMathInElement !== "function") {
          await loadScript(baseUrl + "/contrib/auto-render.min.js");
        }
        if (!window.katex || typeof window.renderMathInElement !== "function") {
          throw new Error("KaTeX globals were not registered by " + baseUrl);
        }
        return baseUrl;
      } catch (error) {
        errors.push(error.message);
        console.warn(error.message);
      }
    }

    throw new Error("KaTeX JavaScript could not be loaded from any CDN. " + errors.join(" | "));
  }

  function renderQuillMath(root) {
    if (!window.katex || !root.querySelectorAll) {
      return;
    }
    const embeds = [];
    if (root instanceof Element && root.matches(".ql-math-inline, .ql-math-block")) {
      embeds.push(root);
    }
    embeds.push(...root.querySelectorAll(".ql-math-inline, .ql-math-block"));

    embeds.forEach((node) => {
      if (node.dataset.katexRendered === "true") {
        return;
      }
      const latex = node.dataset.latex || "";
      if (!latex) {
        return;
      }
      node.dataset.katexRendered = "true";
      try {
        window.katex.render(latex, node, {
          throwOnError: false,
          strict: "ignore",
          displayMode: node.classList.contains("ql-math-block")
        });
      } catch (error) {
        delete node.dataset.katexRendered;
        console.error("KaTeX failed to render an editor math object.", error);
      }
    });
  }

  function renderMath(root) {
    if (!mathReady || !window.katex || typeof window.renderMathInElement !== "function") {
      return;
    }

    renderQuillMath(root);
    const elements = [];
    if (root instanceof Element && root.matches(".math-content")) {
      elements.push(root);
    }
    if (root.querySelectorAll) {
      elements.push(...root.querySelectorAll(".math-content"));
    }

    elements.forEach((element) => {
      if (element.dataset.katexRendered === "true") {
        return;
      }
      element.dataset.katexRendered = "true";
      try {
        window.renderMathInElement(element, {
          delimiters,
          throwOnError: false,
          strict: "ignore"
        });
        element.classList.remove("math-render-error");
      } catch (error) {
        delete element.dataset.katexRendered;
        element.classList.add("math-render-error");
        console.error("KaTeX failed to render a content block.", error);
      }
    });
  }

  function showLoadError(error) {
    console.error("HEXORA KaTeX could not be initialized.", error);
    let notice = document.getElementById("katex-load-error");
    if (!notice) {
      notice = document.createElement("div");
      notice.id = "katex-load-error";
      notice.className = "katex-load-error";
      notice.setAttribute("role", "alert");
      const text = document.createElement("span");
      text.textContent = "수식 표시 도구를 불러오지 못했습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "다시 시도";
      retry.addEventListener("click", () => {
        notice.remove();
        start();
      });
      notice.append(text, retry);
      const main = document.querySelector("main") || document.body;
      main.prepend(notice);
    }
  }

  function watchForNewMath() {
    if (observer || typeof MutationObserver !== "function") {
      return;
    }
    observer = new MutationObserver((records) => {
      records.forEach((record) => {
        record.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            renderMath(node);
          }
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function start() {
    if (loadingPromise) {
      return loadingPromise;
    }

    loadingPromise = Promise.all([loadStylesheet(), loadEngine()])
      .then(() => {
        mathReady = true;
        document.documentElement.dataset.katexReady = "true";
        renderMath(document);
        watchForNewMath();
        return true;
      })
      .catch((error) => {
        mathReady = false;
        document.documentElement.dataset.katexReady = "false";
        showLoadError(error);
        return false;
      })
      .finally(() => {
        loadingPromise = null;
      });

    return loadingPromise;
  }

  window.hexoraRenderMath = renderMath;
  window.addEventListener("online", () => {
    if (!mathReady) {
      start();
    }
  });

  start();
})();
