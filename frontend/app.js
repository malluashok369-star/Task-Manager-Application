const API = "/api/tasks";
const state = { tasks: [], filter: "all", search: "", reverse: false, user: null };
const $ = (selector) => document.querySelector(selector);
const taskList = $("#task-list");
const emptyState = $("#empty-state");
const dialog = $("#task-dialog");
const form = $("#task-form");
const filterLabels = { all: "Everything you need to get done", pending: "Tasks that still need your attention", completed: "Look at everything you have accomplished" };

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401 && url.startsWith(API)) showAuth("Your session expired. Please sign in again.");
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).filter(Boolean).join("; ") || message;
    } catch { /* The response may not contain JSON. */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function showAuth(message = "") {
  state.user = null;
  $("#app-shell").hidden = true;
  $("#auth-screen").hidden = false;
  setAuthMode("login");
  $("#auth-error").textContent = message;
  $("#auth-error").hidden = !message;
}

function showDashboard(user) {
  state.user = user;
  const initial = user.name.trim().charAt(0).toUpperCase();
  $("#profile-name").textContent = user.name;
  $("#profile-email").textContent = user.email;
  $("#top-avatar").textContent = initial;
  document.querySelector(".sidebar-footer .avatar").textContent = initial;
  $("#auth-screen").hidden = true;
  $("#app-shell").hidden = false;
  loadTasks();
}

function setAuthMode(mode) {
  const registering = mode === "register";
  $("#auth-name-field").hidden = !registering;
  $("#auth-name").required = registering;
  $("#auth-password").autocomplete = registering ? "new-password" : "current-password";
  $("#login-tab").classList.toggle("active", !registering);
  $("#register-tab").classList.toggle("active", registering);
  $("#login-tab").setAttribute("aria-selected", String(!registering));
  $("#register-tab").setAttribute("aria-selected", String(registering));
  $("#auth-title").textContent = registering ? "Start with a clear plan." : "Good to have you back.";
  $("#auth-subtitle").textContent = registering
    ? "Create your account and keep your tasks yours."
    : "Sign in to pick up where you left off.";
  $("#auth-submit").innerHTML = registering
    ? 'Create account <span class="button-arrow">→</span>'
    : 'Sign in <span class="button-arrow">→</span>';
  $("#auth-error").hidden = true;
  $("#auth-error").textContent = "";
}

async function initializeApp() {
  try {
    const session = await request("/api/auth/me");
    if (session.authenticated) showDashboard(session.user);
    else showAuth();
  } catch (error) {
    showAuth("Could not connect to Daymark. Check that the server is running and try again.");
  }
}

async function submitAuth(event) {
  event.preventDefault();
  const registering = $("#auth-name-field").hidden === false;
  const payload = {
    email: $("#auth-email").value.trim(),
    password: $("#auth-password").value,
  };
  if (registering) payload.name = $("#auth-name").value.trim();
  const button = $("#auth-submit");
  button.disabled = true;
  $("#auth-error").hidden = true;
  try {
    const user = await request(`/api/auth/${registering ? "register" : "login"}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#auth-form").reset();
    showDashboard(user);
  } catch (error) {
    $("#auth-error").textContent = error.message;
    $("#auth-error").hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function signOut() {
  try {
    await request("/api/auth/logout", { method: "POST" });
    state.tasks = [];
    render();
    showAuth();
  } catch (error) {
    toast(error.message, true);
  }
}

function dateLabel(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((date - today) / 86400000);
  const label = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
  return { label: days < 0 ? `Overdue · ${label}` : days === 0 ? "Due today" : days === 1 ? "Due tomorrow" : label, className: days < 0 ? "overdue" : days === 0 ? "today" : "" };
}

function renderTask(task, index) {
  const due = dateLabel(task.due_date);
  const meta = [
    `<span class="priority priority-${escapeHtml(task.priority)}">${escapeHtml(task.priority)} priority</span>`,
    due ? `<span class="due-date ${due.className}">${escapeHtml(due.label)}</span>` : "",
  ].filter(Boolean).join("");
  return `<article class="task-row ${task.completed ? "is-completed" : ""}" style="animation-delay:${Math.min(index, 8) * 25}ms">
    <input class="task-check" type="checkbox" aria-label="${task.completed ? "Mark as in progress" : "Mark as completed"}" data-complete="${task.id}" ${task.completed ? "checked" : ""}>
    <div class="task-main"><div class="task-title-line"><h3 class="task-title">${escapeHtml(task.title)}</h3></div>
      ${task.description ? `<p class="task-description">${escapeHtml(task.description)}</p>` : ""}
      <div class="task-meta">${meta}</div>
    </div>
    <div class="task-actions"><button class="task-action" data-edit="${task.id}" aria-label="Edit ${escapeHtml(task.title)}" title="Edit task">✎</button><button class="task-action delete" data-delete="${task.id}" aria-label="Delete ${escapeHtml(task.title)}" title="Delete task">⌫</button></div>
  </article>`;
}

function updateStats() {
  const total = state.tasks.length;
  const completed = state.tasks.filter((task) => task.completed).length;
  const pending = total - completed;
  const rate = total ? Math.round((completed / total) * 100) : 0;
  $("#stat-total").textContent = total;
  $("#stat-pending").textContent = pending;
  $("#stat-completed").textContent = completed;
  $("#stat-rate").textContent = rate;
  $("#nav-all").textContent = total;
  $("#nav-pending").textContent = pending;
  $("#nav-completed").textContent = completed;
  $("#filter-all-count").textContent = total;
  $("#filter-pending-count").textContent = pending;
  $("#filter-completed-count").textContent = completed;
  $("#sidebar-progress").style.width = `${rate}%`;
  $("#main-progress").style.width = `${rate}%`;
  $("#sidebar-progress-label").textContent = `${rate}%`;
}

function render() {
  updateStats();
  const visible = state.tasks.filter((task) => {
    const matchesFilter = state.filter === "all" || (state.filter === "completed" ? task.completed : !task.completed);
    const query = state.search.toLowerCase();
    return matchesFilter && (!query || `${task.title} ${task.description}`.toLowerCase().includes(query));
  });
  if (state.reverse) visible.reverse();
  taskList.innerHTML = visible.map(renderTask).join("");
  taskList.hidden = visible.length === 0;
  emptyState.hidden = visible.length !== 0;
  $("#heading-count").textContent = visible.length;
  $("#section-caption").textContent = filterLabels[state.filter];
  $("#list-summary").textContent = `Showing ${visible.length} ${visible.length === 1 ? "task" : "tasks"}`;
  if (state.search) {
    $("#empty-title").textContent = "No matches found.";
    $("#empty-copy").textContent = "Try another search or clear the search field.";
    $("#empty-add-button").hidden = true;
  } else {
    $("#empty-title").textContent = state.filter === "completed" ? "Nothing completed yet." : state.filter === "pending" ? "All caught up." : "A clear slate.";
    $("#empty-copy").textContent = state.filter === "completed" ? "Completed tasks will show up here." : state.filter === "pending" ? "You have no tasks waiting. Enjoy the breathing room." : "Add your first task and make it a great day.";
    $("#empty-add-button").hidden = state.filter === "completed";
  }
}

async function loadTasks() {
  taskList.hidden = false;
  taskList.innerHTML = '<div class="loading">Getting your tasks ready...</div>';
  try {
    state.tasks = await request(API);
    render();
  } catch (error) {
    taskList.innerHTML = "";
    emptyState.hidden = false;
    $("#empty-title").textContent = "Could not load your tasks.";
    $("#empty-copy").textContent = error.message;
    $("#empty-add-button").hidden = true;
    toast(error.message, true);
  }
}

function toast(message, error = false) {
  const element = document.createElement("div");
  element.className = `toast${error ? " error" : ""}`;
  element.textContent = message;
  $("#toast-region").append(element);
  window.setTimeout(() => element.remove(), 3000);
}

function openDialog(task = null) {
  form.reset();
  $("#task-id").value = task?.id ?? "";
  $("#task-title").value = task?.title ?? "";
  $("#task-description").value = task?.description ?? "";
  $("#task-due").value = task?.due_date ?? "";
  $("#task-priority").value = task?.priority ?? "medium";
  $("#dialog-title").textContent = task ? "Edit task" : "Create a task";
  $("#save-task-button").innerHTML = task ? "Save changes <span class=\"button-arrow\">→</span>" : "Create task <span class=\"button-arrow\">→</span>";
  dialog.showModal();
  requestAnimationFrame(() => $("#task-title").focus());
}

function closeDialog() {
  dialog.close();
}

async function saveTask(event) {
  event.preventDefault();
  const id = $("#task-id").value;
  const payload = {
    title: $("#task-title").value.trim(),
    description: $("#task-description").value.trim(),
    due_date: $("#task-due").value || null,
    priority: $("#task-priority").value,
  };
  const button = $("#save-task-button");
  button.disabled = true;
  try {
    await request(id ? `${API}/${id}` : API, { method: id ? "PATCH" : "POST", body: JSON.stringify(payload) });
    closeDialog();
    await loadTasks();
    toast(id ? "Task updated" : "Task created");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function setFilter(filter) {
  state.filter = filter;
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.classList.toggle("active", button.classList.contains("nav-item") && button.dataset.filter === filter);
    button.classList.toggle("selected", button.classList.contains("filter-chip") && button.dataset.filter === filter);
  });
  render();
}

taskList.addEventListener("click", async (event) => {
  const editButton = event.target.closest("[data-edit]");
  const deleteButton = event.target.closest("[data-delete]");
  if (editButton) {
    const task = state.tasks.find((item) => item.id === Number(editButton.dataset.edit));
    if (task) openDialog(task);
  }
  if (deleteButton) {
    const task = state.tasks.find((item) => item.id === Number(deleteButton.dataset.delete));
    if (!task || !window.confirm(`Delete "${task.title}"? This can't be undone.`)) return;
    try {
      await request(`${API}/${task.id}`, { method: "DELETE" });
      await loadTasks();
      toast("Task deleted");
    } catch (error) { toast(error.message, true); }
  }
});

taskList.addEventListener("change", async (event) => {
  const checkbox = event.target.closest("[data-complete]");
  if (!checkbox) return;
  checkbox.disabled = true;
  try {
    await request(`${API}/${checkbox.dataset.complete}`, { method: "PATCH", body: JSON.stringify({ completed: checkbox.checked }) });
    await loadTasks();
    toast(checkbox.checked ? "Task completed — nice work!" : "Task moved back to in progress");
  } catch (error) {
    checkbox.checked = !checkbox.checked;
    checkbox.disabled = false;
    toast(error.message, true);
  }
});

document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.filter)));
$("#login-tab").addEventListener("click", () => setAuthMode("login"));
$("#register-tab").addEventListener("click", () => setAuthMode("register"));
$("#auth-form").addEventListener("submit", submitAuth);
$("#logout-button").addEventListener("click", signOut);
$("#top-logout-button").addEventListener("click", signOut);
$("#add-task-button").addEventListener("click", () => openDialog());
$("#empty-add-button").addEventListener("click", () => openDialog());
$("#close-dialog").addEventListener("click", closeDialog);
$("#cancel-dialog").addEventListener("click", closeDialog);
$("#task-form").addEventListener("submit", saveTask);
$("#search-input").addEventListener("input", (event) => { state.search = event.target.value.trim(); render(); });
$("#sort-button").addEventListener("click", () => { state.reverse = !state.reverse; render(); });
dialog.addEventListener("click", (event) => { if (event.target === dialog) closeDialog(); });
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    $("#search-input").focus();
  }
});
$("#today-label").textContent = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(new Date());
initializeApp();
