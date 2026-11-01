/* Small, offline enhancements. Ordinary links and details work without JS. */
(() => {
  "use strict";

  let printState = null;

  const preparePrint = () => {
    if (printState !== null) return;
    printState = new Map();
    document.querySelectorAll("details").forEach((detail) => {
      printState.set(detail, detail.open);
      detail.open = true;
    });
  };

  const restoreAfterPrint = () => {
    if (printState === null) return;
    printState.forEach((wasOpen, detail) => {
      detail.open = wasOpen;
    });
    printState = null;
  };

  window.addEventListener("beforeprint", preparePrint);
  window.addEventListener("afterprint", restoreAfterPrint);

  // A print media change covers browsers that omit one of the print events.
  if (typeof window.matchMedia === "function") {
    const printMedia = window.matchMedia("print");
    const onPrintChange = (event) => {
      if (event.matches) preparePrint();
      else restoreAfterPrint();
    };
    if (typeof printMedia.addEventListener === "function") {
      printMedia.addEventListener("change", onPrintChange);
    } else if (typeof printMedia.addListener === "function") {
      printMedia.addListener(onPrintChange);
    }
  }

  const toc = document.querySelector(".reader-sidebar .toc");
  const content = document.querySelector(".reader-main .course-content");
  if (!toc || !content) return;

  const disclosure = toc.querySelector(".toc-disclosure");
  const desktopMedia = typeof window.matchMedia === "function"
    ? window.matchMedia("(min-width: 64rem)")
    : null;

  const syncDisclosure = () => {
    if (!disclosure || !desktopMedia) return;
    // Printing can change viewport media; preserve the original disclosure state.
    if (printState === null) disclosure.open = desktopMedia.matches;
  };
  syncDisclosure();
  if (desktopMedia) {
    if (typeof desktopMedia.addEventListener === "function") {
      desktopMedia.addEventListener("change", syncDisclosure);
    } else if (typeof desktopMedia.addListener === "function") {
      desktopMedia.addListener(syncDisclosure);
    }
  }

  const entries = Array.from(toc.querySelectorAll('a[href^="#"]'))
    .map((link) => {
      let id;
      try {
        id = decodeURIComponent(link.getAttribute("href").slice(1));
      } catch {
        return null;
      }
      const heading = document.getElementById(id);
      return heading && content.contains(heading) && heading.matches("h1, h2, h3")
        ? { link, heading }
        : null;
    })
    .filter(Boolean);

  toc.addEventListener("click", (event) => {
    if (!disclosure || !desktopMedia || desktopMedia.matches) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href]");
    const target = link && (link.getAttribute("target") || "_self").toLowerCase();
    if (link && toc.contains(link) && ["_self", "_top", "_parent"].includes(target) &&
        link.getAttribute("download") === null) {
      disclosure.open = false;
    }
    // The browser follows ordinary anchors and chapter links with native history.
  });

  if (!entries.length) return;
  const sidebar = toc.closest(".reader-sidebar");
  let active = null;
  let framePending = false;

  const updateCurrentSection = () => {
    framePending = false;
    const activationLine = Math.min(window.innerHeight * 0.25, 160);
    let current = entries[0];
    for (const entry of entries) {
      if (entry.heading.getBoundingClientRect().top > activationLine) break;
      current = entry;
    }
    // A short final section may never reach the activation line.
    if (window.scrollY > 0 &&
        window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) {
      current = entries[entries.length - 1];
    }
    if (active === current) return;
    entries.forEach(({ link }) => {
      if (link.getAttribute("aria-current") === "location") link.removeAttribute("aria-current");
    });
    if (current.link.getAttribute("aria-current") !== "page") {
      current.link.setAttribute("aria-current", "location");
    }
    active = current;

    // Keep the current item visible by moving only the index's own scroll area.
    if (sidebar && desktopMedia && desktopMedia.matches) {
      const item = current.link.getBoundingClientRect();
      const bounds = sidebar.getBoundingClientRect();
      if (item.top < bounds.top + 8) sidebar.scrollTop += item.top - bounds.top - 8;
      else if (item.bottom > bounds.bottom - 8) sidebar.scrollTop += item.bottom - bounds.bottom + 8;
    }
  };

  const scheduleUpdate = () => {
    if (framePending) return;
    framePending = true;
    window.requestAnimationFrame(updateCurrentSection);
  };

  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener("resize", scheduleUpdate);
  window.addEventListener("hashchange", scheduleUpdate);
  window.addEventListener("afterprint", scheduleUpdate);
  scheduleUpdate();
})();
