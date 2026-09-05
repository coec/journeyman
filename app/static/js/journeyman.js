"use strict";

window.Journeyman = {

    initActionMenus() {

        const menus = Array.from(
            document.querySelectorAll("details.action-menu"),
        );

        if (!menus.length) {
            return;
        }

        menus.forEach(function (menu) {
            menu.addEventListener("toggle", function () {
                if (!menu.open) {
                    menu.querySelectorAll("details.action-submenu[open]").forEach(
                        function (submenu) {
                            submenu.open = false;
                        },
                    );
                    return;
                }

                menus.forEach(function (otherMenu) {
                    if (otherMenu !== menu) {
                        otherMenu.open = false;
                    }
                });
            });
        });

        document.addEventListener("click", function (event) {
            menus.forEach(function (menu) {
                if (menu.open && !menu.contains(event.target)) {
                    menu.open = false;
                }
            });
        });

        document.addEventListener("keydown", function (event) {
            if (event.key !== "Escape") {
                return;
            }

            const openMenu = menus.find(function (menu) {
                return menu.open;
            });

            if (!openMenu) {
                return;
            }

            const openSubmenu = openMenu.querySelector(
                "details.action-submenu[open]",
            );
            if (openSubmenu) {
                openSubmenu.open = false;
                openSubmenu.querySelector("summary")?.focus();
                return;
            }

            openMenu.open = false;
            openMenu.querySelector("summary")?.focus();
        });

    },

    initAboutDialog() {

        const modal = document.getElementById("journeyman-about-modal");
        const openButton = document.querySelector("[data-about-open]");
        if (!modal || !openButton) {
            return;
        }

        const closeButtons = Array.from(
            modal.querySelectorAll("[data-about-close]"),
        );

        function openModal() {
            modal.hidden = false;
            const closeButton = modal.querySelector("button[data-about-close]");
            if (closeButton) {
                closeButton.focus();
            }
        }

        function closeModal() {
            modal.hidden = true;
            openButton.focus();
        }

        openButton.addEventListener("click", openModal);
        closeButtons.forEach(function (button) {
            button.addEventListener("click", closeModal);
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !modal.hidden) {
                closeModal();
            }
        });

    },

    initDispatchProgress() {

        const forms = Array.from(
            document.querySelectorAll(
                "form[data-dispatch-progress], form[data-operation-progress]",
            ),
        );

        if (!forms.length || !("fetch" in window) || !("EventSource" in window)) {
            return;
        }

        let active = false;
        let eventSource = null;

        function progressId() {
            if (window.crypto && typeof window.crypto.randomUUID === "function") {
                return window.crypto.randomUUID();
            }
            return [Date.now().toString(16), Math.random().toString(16).slice(2), Math.random().toString(16).slice(2)].join("-");
        }

        function ensureModal() {
            let modal = document.getElementById("dispatch-progress-modal");
            if (modal) {
                return modal;
            }

            modal = document.createElement("div");
            modal.id = "dispatch-progress-modal";
            modal.className = "dispatch-progress-modal";
            modal.hidden = true;
            modal.innerHTML = `
              <div class="dispatch-progress-backdrop"></div>
              <section class="dispatch-progress-dialog" role="dialog" aria-modal="true" aria-labelledby="dispatch-progress-title">
                <header>
                  <div>
                    <h2 id="dispatch-progress-title">Dispatching</h2>
                    <p id="dispatch-progress-label"></p>
                  </div>
                </header>
                <ol class="dispatch-progress-steps" id="dispatch-progress-steps"></ol>
                <div class="dispatch-progress-current">
                  <span class="dispatch-progress-spinner" aria-hidden="true"></span>
                  <div>
                    <strong id="dispatch-progress-message">Starting dispatch</strong>
                    <div id="dispatch-progress-detail"></div>
                  </div>
                </div>
                <div class="dispatch-progress-error" id="dispatch-progress-error" hidden></div>
                <div class="form-actions dispatch-progress-actions" id="dispatch-progress-actions" hidden>
                  <button class="button" type="button" id="dispatch-progress-close">Close</button>
                </div>
              </section>`;
            document.body.appendChild(modal);
            modal.querySelector("#dispatch-progress-close").addEventListener("click", function () {
                modal.hidden = true;
                active = false;
            });
            return modal;
        }

        function showModal(form) {
            const modal = ensureModal();
            modal.hidden = false;
            modal.dataset.progressErrorTitle = (
                form.dataset.progressErrorTitle || "Dispatch failed"
            );
            modal.querySelector("#dispatch-progress-title").textContent = (
                form.dataset.progressTitle || "Dispatching"
            );
            modal.querySelector("#dispatch-progress-label").textContent = "";
            modal.querySelector("#dispatch-progress-steps").innerHTML = "";
            modal.querySelector("#dispatch-progress-message").textContent = (
                form.dataset.progressStartMessage || "Starting dispatch"
            );
            modal.querySelector("#dispatch-progress-detail").textContent = "";
            modal.querySelector("#dispatch-progress-error").hidden = true;
            modal.querySelector("#dispatch-progress-actions").hidden = true;
            modal.querySelector(".dispatch-progress-current").hidden = false;
            return modal;
        }

        function addCompletedStep(modal, payload) {
            const list = modal.querySelector("#dispatch-progress-steps");
            const previous = list.lastElementChild;
            if (previous && previous.dataset.phase === payload.phase) {
                previous.querySelector("span:last-child").textContent = payload.message;
                return;
            }
            const item = document.createElement("li");
            item.dataset.phase = payload.phase || "";
            item.innerHTML = '<span aria-hidden="true">✓</span><span></span>';
            item.querySelector("span:last-child").textContent = payload.message || payload.phase || "Completed";
            list.appendChild(item);
        }

        function updateProgress(modal, payload) {
            if (payload.label) {
                modal.querySelector("#dispatch-progress-label").textContent = payload.label;
            }

            if (payload.state === "error") {
                modal.querySelector("#dispatch-progress-title").textContent = (
                    modal.dataset.progressErrorTitle || "Dispatch failed"
                );
                modal.querySelector(".dispatch-progress-current").hidden = true;
                const error = modal.querySelector("#dispatch-progress-error");
                error.textContent = payload.message || "Dispatch failed.";
                error.hidden = false;
                modal.querySelector("#dispatch-progress-actions").hidden = false;
                return;
            }

            const message = modal.querySelector("#dispatch-progress-message");
            const detail = modal.querySelector("#dispatch-progress-detail");
            const prior = message.dataset.phase;
            if (prior && prior !== payload.phase) {
                addCompletedStep(modal, {
                    phase: prior,
                    message: message.textContent,
                });
            }
            message.dataset.phase = payload.phase || "";
            message.textContent = payload.message || "Working…";
            detail.textContent = payload.detail || "";

            if (payload.state === "done") {
                addCompletedStep(modal, payload);
                modal.querySelector(".dispatch-progress-current").hidden = true;
            }
        }

        function replaceDocument(html, url) {
            if (url) {
                window.history.pushState({}, "", url);
            }
            document.open();
            document.write(html);
            document.close();
        }

        forms.forEach(function (form) {
            form.addEventListener("submit", async function (event) {
                if (active) {
                    event.preventDefault();
                    return;
                }
                event.preventDefault();
                active = true;

                const modal = showModal(form);
                const id = progressId();
                const progressUrl = `/dispatch-progress/${encodeURIComponent(id)}/events`;
                eventSource = new EventSource(progressUrl);
                eventSource.onmessage = function (progressEvent) {
                    try {
                        const payload = JSON.parse(progressEvent.data);
                        updateProgress(modal, payload);
                        if (payload.state === "done" || payload.state === "error") {
                            eventSource.close();
                            eventSource = null;
                        }
                    } catch (error) {
                        console.warn("Unable to parse dispatch progress", error);
                    }
                };

                const method = String(form.method || "GET").toUpperCase();
                const formData = new FormData(form);
                let url = form.action;
                const options = {
                    method: method,
                    credentials: "same-origin",
                    headers: {
                        "X-Journeyman-Dispatch-Progress": id,
                        "X-Requested-With": "JourneymanDispatchProgress",
                    },
                };

                if (method === "GET") {
                    const query = new URLSearchParams(formData);
                    if (query.toString()) {
                        url += (url.includes("?") ? "&" : "?") + query.toString();
                    }
                } else {
                    options.body = formData;
                }

                try {
                    const response = await fetch(url, options);
                    const html = await response.text();
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }

                    if (response.redirected && /\/jobs\/\d+(?:$|[?#])/.test(response.url)) {
                        window.location.assign(response.url);
                        return;
                    }

                    if (response.ok || response.status === 400 || response.status === 409) {
                        const completeDelay = Number.parseInt(
                            form.dataset.progressCompleteDelay || "0",
                            10,
                        );
                        if (completeDelay > 0 && response.ok) {
                            await new Promise(function (resolve) {
                                window.setTimeout(resolve, completeDelay);
                            });
                        }
                        replaceDocument(html, response.url);
                        return;
                    }

                    throw new Error(`Dispatch request failed with HTTP ${response.status}`);
                } catch (error) {
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                    modal.querySelector("#dispatch-progress-title").textContent = (
                        modal.dataset.progressErrorTitle || "Dispatch failed"
                    );
                    modal.querySelector(".dispatch-progress-current").hidden = true;
                    const errorBox = modal.querySelector("#dispatch-progress-error");
                    errorBox.textContent = error.message || "Dispatch request failed.";
                    errorBox.hidden = false;
                    modal.querySelector("#dispatch-progress-actions").hidden = false;
                }
            });
        });

    },

    initBreakGlassSession() {

        const body = document.body;
        if (!body || body.dataset.breakGlass !== "true") {
            return;
        }
        if (body.dataset.breakGlassNonExpiring === "true") {
            return;
        }

        const activatedAt = new Date(body.dataset.breakGlassActivatedAt || "");
        const expiresAt = new Date(body.dataset.breakGlassExpiresAt || "");
        if (Number.isNaN(activatedAt.getTime()) || Number.isNaN(expiresAt.getTime())) {
            return;
        }

        const lifetimeMilliseconds = expiresAt.getTime() - activatedAt.getTime();
        if (lifetimeMilliseconds <= 0) {
            return;
        }
        const warningFractions = [0.5, 0.75, 5 / 6, 11 / 12];
        const warningThresholds = warningFractions.map(function (fraction) {
            return activatedAt.getTime() + (lifetimeMilliseconds * fraction);
        });
        const shownWarnings = new Set();

        function ensureModal() {
            let modal = document.getElementById("break-glass-warning-modal");
            if (modal) {
                return modal;
            }
            modal = document.createElement("div");
            modal.id = "break-glass-warning-modal";
            modal.className = "break-glass-warning-modal";
            modal.hidden = true;
            modal.innerHTML = `
              <div class="break-glass-warning-backdrop"></div>
              <section class="break-glass-warning-dialog" role="dialog" aria-modal="true" aria-labelledby="break-glass-warning-title">
                <h2 id="break-glass-warning-title">Break-glass access expiring</h2>
                <p id="break-glass-warning-message"></p>
                <p>This emergency activation is non-renewable. Signing out expires it immediately.</p>
                <div class="form-actions">
                  <button class="button primary" type="button" data-break-glass-warning-close>OK</button>
                </div>
              </section>`;
            document.body.appendChild(modal);
            modal.querySelector("[data-break-glass-warning-close]").addEventListener("click", function () {
                modal.hidden = true;
            });
            return modal;
        }

        function plural(value, singular) {
            return `${value} ${singular}${value === 1 ? "" : "s"}`;
        }
        function formatRemainingWords(milliseconds) {
            const totalMinutes = Math.max(1, Math.ceil(milliseconds / 60000));
            const days = Math.floor(totalMinutes / 1440);
            const hours = Math.floor((totalMinutes % 1440) / 60);
            const minutes = totalMinutes % 60;
            if (days > 0) {
                return `${plural(days, "day")} ${plural(hours, "hour")} ${plural(minutes, "minute")}`;
            }
            if (hours > 0) {
                return `${plural(hours, "hour")} ${plural(minutes, "minute")}`;
            }
            return plural(totalMinutes, "minute");
        }
        function formatCountdown(milliseconds) {
            const remainingSeconds = Math.ceil(milliseconds / 1000);
            const days = Math.floor(remainingSeconds / 86400);
            const hours = Math.floor((remainingSeconds % 86400) / 3600);
            const minutes = Math.floor((remainingSeconds % 3600) / 60);
            const seconds = remainingSeconds % 60;
            if (days > 0) {
                return `${days}d ${hours}h ${minutes}m`;
            }
            if (hours > 0) {
                return `${hours}h ${minutes}m`;
            }
            return `${minutes}:${String(seconds).padStart(2, "0")}`;
        }

        function showWarning(elapsedMinutes) {
            const modal = ensureModal();
            modal.querySelector("#break-glass-warning-message").textContent =
                `${formatRemainingWords(remainingMilliseconds)} remain before this break-glass activation expires.`;
            modal.hidden = false;
        }

        function update() {
            const now = Date.now();
            const remainingMilliseconds = expiresAt.getTime() - now;
            const countdown = document.querySelector("[data-break-glass-countdown]");

            if (remainingMilliseconds <= 0) {
                if (countdown) {
                    countdown.textContent = "expired";
                }
                window.location.assign("/login");
                return;
            }

            if (countdown) {
                countdown.textContent = formatCountdown(remainingMilliseconds);
            }
            const applicableWarnings = warningThresholds.filter(function (threshold) {
                return threshold <= now;
            });
            if (applicableWarnings.length) {
                const latest = applicableWarnings[applicableWarnings.length - 1];
                warningThresholds.forEach(function (threshold) {
                    if (threshold < latest) {
                        shownWarnings.add(threshold);
                    }
                });
                if (!shownWarnings.has(latest)) {
                    shownWarnings.add(latest);
                    showWarning(remainingMilliseconds);
                }
            }
            window.setTimeout(update, 1000);
        }

        update();

    },

    initInventoryCopyButtons() {

        const buttons = Array.from(
            document.querySelectorAll("button[data-copy-target]"),
        );

        if (!buttons.length) {
            return;
        }

        function legacyCopy(value) {
            const textarea = document.createElement("textarea");
            textarea.value = value;
            textarea.setAttribute("readonly", "");
            textarea.style.position = "fixed";
            textarea.style.left = "-9999px";
            textarea.style.top = "0";
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();

            let copied = false;
            try {
                copied = document.execCommand("copy");
            } finally {
                document.body.removeChild(textarea);
            }
            return copied;
        }

        async function copyContents(button) {
            const target = document.getElementById(button.dataset.copyTarget);
            const status = button.querySelector(".inventory-copy-status");
            if (!target || !status) {
                return;
            }

            const text = target.textContent;
            let copied = false;

            if (
                navigator.clipboard
                && typeof navigator.clipboard.writeText === "function"
            ) {
                try {
                    await navigator.clipboard.writeText(text);
                    copied = true;
                } catch (error) {
                    copied = legacyCopy(text);
                }
            } else {
                copied = legacyCopy(text);
            }

            if (copied) {
                button.classList.add("copied");
                status.textContent = "Copied";
                window.setTimeout(function () {
                    button.classList.remove("copied");
                    status.textContent = "";
                }, 1200);
                return;
            }

            status.textContent = "Copy failed";
            window.setTimeout(function () {
                status.textContent = "";
            }, 1800);
        }

        buttons.forEach(function (button) {
            button.addEventListener("click", function (event) {
                event.preventDefault();
                event.stopPropagation();
                copyContents(button);
            });
        });

    },

    formatUtcDates() {

        document
            .querySelectorAll(".utc-datetime")
            .forEach(function (element) {

                let value = element.dataset.utc;

                if (!value) {
                    return;
                }

                value = value.trim();

                /*
                 * SQLAlchemy may return a naive datetime even when the
                 * database value represents UTC. If no timezone suffix
                 * exists, explicitly mark it as UTC.
                 */
                const hasTimezone = (
                    value.endsWith("Z")
                    || /[+-]\d{2}:\d{2}$/.test(value)
                );

                if (!hasTimezone) {
                    value += "Z";
                }

                const date = new Date(value);

                if (Number.isNaN(date.getTime())) {
                    console.warn(
                        "Unable to parse UTC datetime:",
                        value,
                    );
                    return;
                }

                const year = date.getFullYear();
                const month = String(
                    date.getMonth() + 1
                ).padStart(2, "0");
                const day = String(
                    date.getDate()
                ).padStart(2, "0");

                const hour = String(
                    date.getHours()
                ).padStart(2, "0");
                const minute = String(
                    date.getMinutes()
                ).padStart(2, "0");
                const second = String(
                    date.getSeconds()
                ).padStart(2, "0");

                const timezone = Intl.DateTimeFormat(
                    undefined,
                    {
                        timeZoneName: "short",
                    },
                )
                .formatToParts(date)
                .find(
                    part => part.type === "timeZoneName"
                )?.value ?? "";

                element.textContent =
                    `${year}-${month}-${day} `
                    + `${hour}:${minute}:${second} `
                    + `${timezone}`;

                element.title = (
                    date.toISOString()
                    .replace("T", " ")
                    .replace(".000Z", " UTC")
                );
            });

    },

};

document.addEventListener(
    "DOMContentLoaded",
    function () {
        Journeyman.initActionMenus();
        Journeyman.initAboutDialog();
        Journeyman.initDispatchProgress();
        Journeyman.initBreakGlassSession();
        Journeyman.initInventoryCopyButtons();
        Journeyman.formatUtcDates();
    },
);

(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    const form = event.target.closest("form[data-confirm]");
    if (!form) {
      return;
    }
    if (!window.confirm(form.dataset.confirm || "Continue?")) {
      event.preventDefault();
    }
  });

  document.addEventListener("change", function (event) {
    const control = event.target.closest("[data-submit-on-change]");
    if (control && control.form) {
      control.form.submit();
    }
  });

  document.addEventListener("click", function (event) {
    const control = event.target.closest("[data-select-on-click]");
    if (control && typeof control.select === "function") {
      control.select();
    }
  });
}());

(function () {
  "use strict";

  const activityLink = document.querySelector("[data-navigation-status-url]");
  const activityCount = document.querySelector("[data-current-activity-count]");

  if (!activityLink || !activityCount || !("fetch" in window)) {
    return;
  }

  const statusUrl = activityLink.dataset.navigationStatusUrl;

  async function refreshNavigationStatus() {
    if (document.hidden) {
      return;
    }

    try {
      const response = await fetch(statusUrl, {
        credentials: "same-origin",
        headers: {"Accept": "application/json"},
        cache: "no-store",
      });
      if (!response.ok) {
        return;
      }

      const payload = await response.json();
      const runningJobs = Number(payload.running_jobs || 0);
      activityCount.textContent = String(runningJobs);
      activityLink.setAttribute(
        "aria-label",
        `Current activities: ${runningJobs} executing Jobs`
      );
    } catch (_error) {
      // The page remains usable with the server-rendered count.
    }
  }

  window.setInterval(refreshNavigationStatus, 5000);
  document.addEventListener("visibilitychange", refreshNavigationStatus);
}());
