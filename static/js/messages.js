document.addEventListener("DOMContentLoaded", function () {
  console.log("✅ messages.js loaded");

  const dataTag = document.getElementById("django-messages-json");
  if (!dataTag) return;

  let messages = [];

  try {
    messages = JSON.parse(dataTag.textContent);
  } catch (e) {
    console.error("⚠️ Failed to parse messages JSON", e);
    return;
  }

  console.log("📨 Parsed Django Messages:", messages);

  if (!messages.length) return;

  const container = document.createElement("div");
  container.className = "toast-container";
  document.body.appendChild(container);

  messages.forEach(msg => {
    const toast = document.createElement("div");
    toast.classList.add("toast", `toast-${msg.tags}`);
    toast.innerHTML = `
      <span class="toast-icon">${msg.tags.includes("success") ? "✔" : msg.tags.includes("error") ? "✖" : "ℹ"}</span>
      <span class="toast-message">${msg.message}</span>
      <button class="toast-close" aria-label="Close">&times;</button>
    `;

    toast.querySelector(".toast-close").addEventListener("click", () => {
      hideToast(toast);
    });

    container.appendChild(toast);

    // Animate in
    setTimeout(() => toast.classList.add("show"), 10);

    // Auto-dismiss after 3 seconds
    setTimeout(() => hideToast(toast), 3000);
  });

  function hideToast(toast) {
    toast.classList.remove("show");
    toast.addEventListener("transitionend", () => toast.remove());
  }
});
