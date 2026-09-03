# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

"""Qt UI: chat dock, API key dialog, memory dialog, settings dialog. Works on PySide2 and PySide6
through FreeCAD's `PySide` shim."""
import base64
import re
import threading
import traceback
from html import escape as html_escape

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from . import agent as agent_mod
from . import client as claude
from . import config as config_mod
from . import history as history_mod

VERSION = "1.0.3"

_dock = None
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = agent_mod.Agent(config_mod.Config.load())
    return _agent


def reload_config():
    cfg = config_mod.Config.load()
    get_agent().set_config(cfg)
    return cfg


def main_window():
    return Gui.getMainWindow()


def run_dialog(dlg):
    """exec_() on PySide2, exec() on PySide6."""
    fn = getattr(dlg, "exec_", None) or dlg.exec
    return fn()


# ------------------------------------------------------------------ GUI-thread bridge

class MainBridge(QtCore.QObject):
    """Lets the worker thread run a callable on the GUI thread and wait for its result."""
    request = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.moveToThread(QtWidgets.QApplication.instance().thread())
        self.request.connect(self._run, QtCore.Qt.QueuedConnection)
        self._lock = threading.Lock()
        self._fn = None
        self._result = None
        self._error = None
        self._done = threading.Event()

    def call(self, fn):
        if QtCore.QThread.currentThread() is QtWidgets.QApplication.instance().thread():
            return fn()
        with self._lock:
            self._fn = fn
            self._result = None
            self._error = None
            self._done.clear()
            self.request.emit()
            self._done.wait()
            if self._error is not None:
                raise self._error
            return self._result

    @QtCore.Slot()
    def _run(self):
        try:
            self._result = self._fn()
        except Exception as ex:
            self._error = ex
        finally:
            self._done.set()


# ------------------------------------------------------------------ dialogs

class ApiKeyDialog(QtWidgets.QDialog):
    """Set / test / remove the key of one provider; saving also makes that provider active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FreeGAD - API key")
        self.setMinimumWidth(560)
        self.cfg = config_mod.Config.load()
        lay = QtWidgets.QVBoxLayout(self)

        prov = QtWidgets.QHBoxLayout()
        prov.addWidget(QtWidgets.QLabel("Provider:"))
        self.rb_anthropic = QtWidgets.QRadioButton("Anthropic (Claude)")
        self.rb_openai = QtWidgets.QRadioButton("OpenAI-compatible (OpenAI, OpenRouter, …)")
        (self.rb_openai if self.cfg.is_openai else self.rb_anthropic).setChecked(True)
        prov.addWidget(self.rb_anthropic)
        prov.addWidget(self.rb_openai)
        prov.addStretch(1)
        lay.addLayout(prov)

        base_row = QtWidgets.QHBoxLayout()
        self.base_label = QtWidgets.QLabel("Base URL:")
        self.base_url = QtWidgets.QComboBox()
        self.base_url.setEditable(True)
        self.base_url.addItems(config_mod.KNOWN_BASE_URLS)
        self.base_url.setCurrentText(self.cfg.openai_base_url)
        base_row.addWidget(self.base_label)
        base_row.addWidget(self.base_url, 1)
        lay.addLayout(base_row)

        self.status = QtWidgets.QLabel("")
        lay.addWidget(self.status)
        self.link = QtWidgets.QLabel("")
        self.link.setOpenExternalLinks(True)
        lay.addWidget(self.link)

        self.edit = QtWidgets.QLineEdit()
        self.edit.setEchoMode(QtWidgets.QLineEdit.Password)
        lay.addWidget(self.edit)

        self.show_cb = QtWidgets.QCheckBox("Show key")
        self.show_cb.toggled.connect(lambda on: self.edit.setEchoMode(
            QtWidgets.QLineEdit.Normal if on else QtWidgets.QLineEdit.Password))
        lay.addWidget(self.show_cb)

        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)

        btns = QtWidgets.QHBoxLayout()
        self.test_btn = QtWidgets.QPushButton("Test")
        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setDefault(True)
        self.remove_btn = QtWidgets.QPushButton("Remove stored key")
        cancel = QtWidgets.QPushButton("Cancel")
        for b in (self.test_btn, self.save_btn, self.remove_btn, cancel):
            btns.addWidget(b)
        lay.addLayout(btns)

        self.test_btn.clicked.connect(self.on_test)
        self.save_btn.clicked.connect(self.on_save)
        self.remove_btn.clicked.connect(self.on_remove)
        cancel.clicked.connect(self.reject)
        self.rb_anthropic.toggled.connect(lambda _on: self.refresh())
        self.refresh()

    def provider(self):
        return config_mod.PROVIDER_OPENAI if self.rb_openai.isChecked() else config_mod.PROVIDER_ANTHROPIC

    def _base_url(self):
        return self.base_url.currentText().strip().rstrip("/")

    def _key(self):
        return self.edit.text().strip()

    def refresh(self):
        openai = self.provider() == config_mod.PROVIDER_OPENAI
        self.base_label.setEnabled(openai)
        self.base_url.setEnabled(openai)
        self.edit.setPlaceholderText("sk-…" if openai else "sk-ant-…")
        key, src = self.cfg.key_for(self.provider())
        env = "OPENAI_API_KEY" if openai else "ANTHROPIC_API_KEY"
        self.status.setText("No key stored for this provider." if not key else (
            "A key is stored (encrypted for this Windows user)." if src == "config"
            else "Using %s from the environment." % env))
        self.remove_btn.setEnabled(src == "config")
        self.link.setText(
            'Get a key at <a href="https://platform.openai.com/api-keys">platform.openai.com</a> or '
            '<a href="https://openrouter.ai/keys">openrouter.ai</a> (set the base URL accordingly)' if openai else
            'Get a key at <a href="https://console.anthropic.com/settings/keys">console.anthropic.com</a>')

    def on_test(self):
        k = self._key()
        if not k:
            self.msg.setText("Enter a key first.")
            return
        self.msg.setText("Checking…")
        QtWidgets.QApplication.processEvents()
        ok, m = claude.verify_api_key(k, self.provider(), self._base_url())
        self.msg.setText(("✔ " if ok else "✖ ") + m)

    def on_save(self):
        k = self._key()
        if not k:
            self.msg.setText("Enter a key first.")
            return
        openai = self.provider() == config_mod.PROVIDER_OPENAI
        if openai and not self._base_url().startswith("http"):
            self.msg.setText("Enter the base URL of the API, e.g. https://api.openai.com/v1 or https://openrouter.ai/api/v1.")
            return
        if not openai and not k.startswith("sk-ant-"):
            if QtWidgets.QMessageBox.question(
                    self, "FreeGAD", "This doesn't look like an Anthropic key (sk-ant-…). Save anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
                return
        ok, m = claude.verify_api_key(k, self.provider(), self._base_url())
        if not ok:
            if QtWidgets.QMessageBox.question(
                    self, "FreeGAD", m + "\n\nSave it anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
                return
        try:
            config_mod.save_api_key(k, self.provider(), base_url=self._base_url() if openai else None)
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "FreeGAD", "Could not save the key:\n" + str(ex))
            return
        reload_config()
        App.Console.PrintMessage("FreeGAD: %s API key saved to %s\n" % (self.provider(), config_mod.file_path()))
        self.accept()

    def on_remove(self):
        if QtWidgets.QMessageBox.question(
                self, "FreeGAD", "Remove the stored %s API key?" % self.provider(),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        config_mod.clear_api_key(self.provider())
        reload_config()
        self.accept()


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FreeGAD - Settings")
        cfg = config_mod.Config.load()
        self.cfg = cfg
        self._models = {config_mod.PROVIDER_ANTHROPIC: cfg.anthropic_model, config_mod.PROVIDER_OPENAI: cfg.openai_model}
        self._current = None
        form = QtWidgets.QFormLayout(self)

        self.provider = QtWidgets.QComboBox()
        self.provider.addItems(["Anthropic (Claude)", "OpenAI-compatible (OpenAI, OpenRouter, …)"])
        self.provider.setCurrentIndex(1 if cfg.is_openai else 0)
        form.addRow("Provider", self.provider)
        self.key_status = QtWidgets.QLabel("")
        form.addRow("", self.key_status)

        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)
        form.addRow("Model", self.model)

        self.base_url = QtWidgets.QComboBox()
        self.base_url.setEditable(True)
        self.base_url.addItems(config_mod.KNOWN_BASE_URLS)
        self.base_url.setCurrentText(cfg.openai_base_url)
        form.addRow("Base URL", self.base_url)

        self.effort = QtWidgets.QComboBox()
        self.effort.addItems(config_mod.EFFORTS)
        self.effort.setCurrentText(cfg.effort)
        self.effort.setToolTip("Anthropic: effort low…max. OpenAI-compatible: sent as reasoning_effort low/medium/high.")
        form.addRow("Effort", self.effort)

        self.max_tokens = QtWidgets.QSpinBox()
        self.max_tokens.setRange(1024, 128000)
        self.max_tokens.setSingleStep(1024)
        self.max_tokens.setValue(cfg.max_tokens)
        form.addRow("Max output tokens", self.max_tokens)

        self.fallbacks = QtWidgets.QCheckBox("Server-side refusal fallback to another model (Anthropic only)")
        self.fallbacks.setChecked(cfg.fallbacks)
        form.addRow(self.fallbacks)

        self.auto = QtWidgets.QCheckBox("Auto-approve edits (no confirmation dialogs)")
        self.auto.setChecked(cfg.auto_approve)
        form.addRow(self.auto)

        self.script_timeout = QtWidgets.QSpinBox()
        self.script_timeout.setRange(0, 3600)
        self.script_timeout.setSingleStep(30)
        self.script_timeout.setSuffix(" s")
        self.script_timeout.setSpecialValueText("no limit")
        self.script_timeout.setValue(cfg.script_timeout)
        self.script_timeout.setToolTip("A run_python script running longer than this is aborted (the GUI is blocked "
                                       "while it runs). 0 = never abort - for heavy jobs you are willing to wait for.")
        form.addRow("Script time limit", self.script_timeout)

        self.memory_guard = QtWidgets.QCheckBox("Memory guard: abort scripts that eat most of the RAM")
        self.memory_guard.setChecked(cfg.memory_guard)
        self.memory_guard.setToolTip("Aborts a run_python script once it has grown FreeCAD's memory by more than half "
                                     "of the machine's RAM or less than ~8% is free. A runaway boolean/tessellation loop "
                                     "can otherwise push Windows into swapping so hard that only a reboot helps. "
                                     "Turn off only if you know the job legitimately needs that much memory.")
        form.addRow(self.memory_guard)

        self.telemetry = QtWidgets.QCheckBox("Collect anonymous usage data")
        self.telemetry.setToolTip("Sends token usage, latencies, tool timings, GUI hangs and error classes keyed by a "
                                  "random install id. Never prompts, answers, code, file names or the API key.")
        self.telemetry.setChecked(cfg.telemetry)
        form.addRow(self.telemetry)

        form.addRow(QtWidgets.QLabel("Config file: " + config_mod.file_path() +
                                     "\nAPI keys are set with FreeGAD > Set API key (one per provider)."))

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.on_ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

        self.provider.currentIndexChanged.connect(lambda _i: self.on_provider())
        self.on_provider()

    def _prov(self):
        return config_mod.PROVIDER_OPENAI if self.provider.currentIndex() == 1 else config_mod.PROVIDER_ANTHROPIC

    def on_provider(self):
        if self._current:
            self._models[self._current] = self.model.currentText().strip()
        p = self._prov()
        self._current = p
        openai = p == config_mod.PROVIDER_OPENAI
        self.model.clear()
        self.model.addItems(config_mod.KNOWN_OPENAI_MODELS if openai else config_mod.KNOWN_MODELS)
        self.model.setCurrentText(self._models.get(p) or
                                  (config_mod.DEFAULT_OPENAI_MODEL if openai else config_mod.DEFAULT_MODEL))
        self.base_url.setEnabled(openai)
        self.fallbacks.setEnabled(not openai)
        key, src = self.cfg.key_for(p)
        self.key_status.setText("No key for this provider yet - use FreeGAD > Set API key." if not key else
                                ("Key: from %s" % ("OPENAI_API_KEY" if openai else "ANTHROPIC_API_KEY")) if src == "env"
                                else "Key: saved (encrypted).")

    def on_ok(self):
        p = self._prov()
        openai = p == config_mod.PROVIDER_OPENAI
        self._models[p] = self.model.currentText().strip()
        base = self.base_url.currentText().strip().rstrip("/")
        if openai and not base.startswith("http"):
            QtWidgets.QMessageBox.warning(self, "FreeGAD",
                                          "Enter the base URL of the API, e.g. https://api.openai.com/v1 or https://openrouter.ai/api/v1.")
            return
        config_mod.save_settings(provider=p,
                                 model=self._models[config_mod.PROVIDER_ANTHROPIC] or config_mod.DEFAULT_MODEL,
                                 openai_model=self._models[config_mod.PROVIDER_OPENAI] or config_mod.DEFAULT_OPENAI_MODEL,
                                 openai_base_url=base or config_mod.DEFAULT_OPENAI_BASE_URL,
                                 effort=self.effort.currentText(),
                                 max_tokens=self.max_tokens.value(),
                                 fallbacks=self.fallbacks.isChecked(),
                                 auto_approve=self.auto.isChecked(),
                                 telemetry=self.telemetry.isChecked(),
                                 script_timeout=self.script_timeout.value(),
                                 memory_guard=self.memory_guard.isChecked())
        try:
            from . import dm as _dm
            _dm.event("settings", {"provider": p, "telemetry": bool(self.telemetry.isChecked()),
                                   "auto_approve": bool(self.auto.isChecked())})
        except Exception:
            pass
        reload_config()
        if _dock:
            _dock.sync_from_config()
        self.accept()


class MemoryDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FreeGAD - Memory")
        self.resize(720, 480)
        self.doc = App.ActiveDocument
        self.user_mem, self.doc_mem = get_agent().memories(self.doc)

        lay = QtWidgets.QVBoxLayout(self)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Scope", "Id", "Category", "Note", "Created"])
        self.tree.setRootIsDecorated(False)
        lay.addWidget(self.tree)

        btns = QtWidgets.QHBoxLayout()
        del_btn = QtWidgets.QPushButton("Delete selected")
        clear_user = QtWidgets.QPushButton("Clear user memory")
        clear_doc = QtWidgets.QPushButton("Clear document memory")
        clear_hist = QtWidgets.QPushButton("Clear chat history")
        clear_hist.setToolTip("Delete the saved transcript for this document (" + history_mod.history_dir() + ")")
        close = QtWidgets.QPushButton("Close")
        for b in (del_btn, clear_user, clear_doc, clear_hist, close):
            btns.addWidget(b)
        lay.addLayout(btns)
        del_btn.clicked.connect(self.on_delete)
        clear_user.clicked.connect(lambda: self.on_clear(self.user_mem))
        clear_doc.clicked.connect(lambda: self.on_clear(self.doc_mem))
        clear_hist.clicked.connect(self.on_clear_history)
        close.clicked.connect(self.accept)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        for m in (self.doc_mem, self.user_mem):
            if m is None:
                continue
            for e in m.entries:
                it = QtWidgets.QTreeWidgetItem([f"{m.scope} ({m.title})", e.id, e.category or "",
                                                e.text, e.created[:10]])
                it.setData(0, QtCore.Qt.UserRole, m.scope)
                self.tree.addTopLevelItem(it)
        for i in range(5):
            self.tree.resizeColumnToContents(i)

    def on_delete(self):
        for it in self.tree.selectedItems():
            m = self.user_mem if it.data(0, QtCore.Qt.UserRole) == "user" else self.doc_mem
            m.remove(it.text(1))
        self._after_change()

    def on_clear(self, m):
        if m is None:
            return
        if QtWidgets.QMessageBox.question(
                self, "FreeGAD", f"Clear all {m.scope} memory?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes:
            m.clear()
            self._after_change()

    def on_clear_history(self):
        if QtWidgets.QMessageBox.question(
                self, "FreeGAD", "Delete the saved chat history for this document?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes:
            if _dock is not None:
                _dock.clear_history()
            else:
                history_mod.History(self.doc).clear()
            get_agent().reset(self.doc)

    def _after_change(self):
        get_agent().reset(self.doc)   # next turn rebuilds the system prompt from the edited stores
        self.refresh()


_session_auto_approve = False


def confirm_dialog(title, text):
    global _session_auto_approve
    if _session_auto_approve:
        return True
    dlg = QtWidgets.QDialog(main_window())
    dlg.setWindowTitle("FreeGAD wants to: " + title)
    dlg.resize(640, 420)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.addWidget(QtWidgets.QLabel("Claude wants to apply this change to the document. Allow?"))
    view = QtWidgets.QPlainTextEdit(text)
    view.setReadOnly(True)
    view.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
    lay.addWidget(view)
    bb = QtWidgets.QDialogButtonBox()
    allow = bb.addButton("Allow", QtWidgets.QDialogButtonBox.AcceptRole)
    allow_all = bb.addButton("Allow all this session", QtWidgets.QDialogButtonBox.ActionRole)
    allow_all.setToolTip("Skip confirmations until FreeCAD is closed (the saved Auto-approve setting is not changed)")
    bb.addButton("Deny", QtWidgets.QDialogButtonBox.RejectRole)
    allow.setDefault(True)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)

    def _allow_all():
        global _session_auto_approve
        _session_auto_approve = True
        dlg.accept()
    allow_all.clicked.connect(_allow_all)
    lay.addWidget(bb)
    return run_dialog(dlg) == QtWidgets.QDialog.Accepted


# ------------------------------------------------------------------ image attachments

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
MAX_IMAGE_DIM = 1568        # Anthropic's optimal long side; larger is downscaled server-side anyway
MAX_PNG_B64 = 1_500_000     # ~1.1 MB binary; beyond this re-encode as JPEG


def encode_image(img):
    """QImage -> (media_type, base64 str). Downscales large images; falls back to JPEG when a
    PNG would be very big (photos). Returns None for an unreadable image."""
    if img is None or img.isNull():
        return None
    if max(img.width(), img.height()) > MAX_IMAGE_DIM:
        img = img.scaled(MAX_IMAGE_DIM, MAX_IMAGE_DIM,
                         QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def save(image, fmt, quality=-1):
        ba = QtCore.QByteArray()
        buf = QtCore.QBuffer(ba)
        buf.open(QtCore.QIODevice.WriteOnly)
        image.save(buf, fmt, quality)
        buf.close()
        return base64.b64encode(bytes(ba)).decode("ascii")

    b64 = save(img, "PNG")
    if len(b64) > MAX_PNG_B64:
        return "image/jpeg", save(img.convertToFormat(QtGui.QImage.Format_RGB32), "JPEG", 85)
    return "image/png", b64


class ChatInput(QtWidgets.QPlainTextEdit):
    """Text input that also takes images: pasted from the clipboard (screenshots, copied images)
    or pasted/dropped image files. Emits image_pasted(QImage) instead of inserting them as text."""
    image_pasted = QtCore.Signal(object)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QtGui.QPixmap):
                img = img.toImage()
            if isinstance(img, QtGui.QImage) and not img.isNull():
                self.image_pasted.emit(img)
                return
        if source.hasUrls():
            handled = False
            for url in source.urls():
                path = url.toLocalFile()
                if path and path.lower().endswith(IMAGE_EXTS):
                    img = QtGui.QImage(path)
                    if not img.isNull():
                        self.image_pasted.emit(img)
                        handled = True
            if handled:
                return
        super().insertFromMimeData(source)


# ------------------------------------------------------------------ worker

class Worker(QtCore.QThread):
    text = QtCore.Signal(str)
    status = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    finished_turn = QtCore.Signal()

    def __init__(self, agent, doc, prompt, bridge, images=None):
        super().__init__()
        self.agent = agent
        self.doc = doc
        self.prompt = prompt
        self.bridge = bridge
        self.images = images or []

    def run(self):
        try:
            self.agent.ask(self.doc, self.prompt,
                           output=self.text.emit,
                           status=self.status.emit,
                           main_call=self.bridge.call,
                           confirm=lambda t, x: self.bridge.call(lambda: confirm_dialog(t, x)),
                           images=self.images)
        except Exception as ex:
            self.failed.emit(str(ex) + "\n" + traceback.format_exc(limit=3))
        finally:
            self.finished_turn.emit()


# ------------------------------------------------------------------ chat dock

class ChatDock(QtWidgets.QDockWidget):
    def __init__(self, parent):
        super().__init__("FreeGAD", parent)
        self.setObjectName("FreeGADDock")
        self.bridge = MainBridge()
        self.worker = None
        self.transcript = []       # list of (role, text) shown in the panel
        self.current = None        # assistant text being streamed
        self.history = None        # history_mod.History for the document the panel currently shows
        self._telemetry_noticed = False
        history_mod.cleanup()

        w = QtWidgets.QWidget()
        self.setWidget(w)
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        bar = QtWidgets.QHBoxLayout()
        self.doc_label = QtWidgets.QLabel("")
        bar.addWidget(self.doc_label, 1)
        self.auto_cb = QtWidgets.QCheckBox("Auto-approve edits")
        self.auto_cb.setToolTip("Skip confirmation dialogs for write tools (run_python etc.)")
        self.auto_cb.toggled.connect(self.on_auto_toggled)
        bar.addWidget(self.auto_cb)
        for label, fn, tip in (("Key", self.on_key, "Set / change the Anthropic API key"),
                               ("Settings", self.on_settings, "Provider, model, effort, tokens"),
                               ("Memory", self.on_memory, "View / edit what FreeGAD remembers"),
                               ("Reset", self.on_reset, "Start a new conversation for this document")):
            b = QtWidgets.QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)

        self.pending_images = []   # list of (media_type, base64, thumbnail button)
        self.attach_row = QtWidgets.QWidget()
        self.attach_lay = QtWidgets.QHBoxLayout(self.attach_row)
        self.attach_lay.setContentsMargins(0, 0, 0, 0)
        self.attach_lay.addStretch(1)
        self.attach_row.hide()
        lay.addWidget(self.attach_row)

        self.input = ChatInput()
        self.input.setPlaceholderText("Ask about the model or tell FreeGAD what to change…  "
                                      "(Ctrl+Enter to send · paste or drop images)")
        self.input.setMaximumHeight(90)
        self.input.installEventFilter(self)
        self.input.image_pasted.connect(self.add_image)
        lay.addWidget(self.input)

        bottom = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("")
        bottom.addWidget(self.status, 1)
        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.clicked.connect(self.on_send)
        bottom.addWidget(self.send_btn)
        lay.addLayout(bottom)

        self.sync_from_config()
        self.refresh_doc_label()
        self.load_history()
        self.show_welcome()

    # -- helpers
    def sync_from_config(self):
        cfg = get_agent().cfg
        self.auto_cb.blockSignals(True)
        self.auto_cb.setChecked(cfg.auto_approve)
        self.auto_cb.blockSignals(False)

    def refresh_doc_label(self):
        d = App.ActiveDocument
        self.doc_label.setText(("<b>%s</b>" % d.Label) if d else "<i>no document open</i>")

    def show_welcome(self):
        cfg = get_agent().cfg
        if not cfg.has_api_key:
            self.append_system("No %s API key set. Click **Key** to enter one." % cfg.provider_label)
        else:
            self.append_system(f"FreeGAD {VERSION} · {cfg.provider_label} · {cfg.model} · effort {cfg.effort}. "
                               "Ask about the active document or tell me what to change.")
            if cfg.telemetry and not self._telemetry_noticed:
                self._telemetry_noticed = True
                self.append_system("Anonymous usage statistics (token counts, timings, hangs - never prompts, "
                                   "code or file names) are collected to improve FreeGAD. Turn off in Settings.")

    def append_system(self, text):
        self.transcript.append(("system", text))
        self.render()

    def load_history(self, force=False):
        """Show the stored transcript of the active document (called when the panel opens or the document changes)."""
        doc = App.ActiveDocument
        h = history_mod.History(doc)
        if self.history is not None and self.history.key == h.key and not force:
            return
        self.history = h
        self.transcript = [(e["role"], e["text"]) for e in h.tail()]
        if self.transcript:
            self.transcript.append(("system", "— earlier conversation restored; %d lines kept in %s —"
                                    % (len(h.entries), h.path)))
        self.render()

    def clear_history(self):
        if self.history is not None:
            self.history.clear()
        self.transcript.clear()
        self.current = None
        self.render()

    # -- rendering (WhatsApp style: assistant left, user right, system notes centred)
    USER_BG, BOT_BG, SYS_FG = "#1f5f4a", "#3a3f46", "#8a9099"

    @staticmethod
    def _md_to_html(md):
        doc = QtGui.QTextDocument()
        if hasattr(doc, "setMarkdown"):
            doc.setMarkdown(md)
        else:
            doc.setPlainText(md)
        html = doc.toHtml()
        m = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
        return m.group(1) if m else html

    def _bubble(self, role, text):
        if role == "system":
            return ('<table width="100%%" cellpadding="2"><tr><td align="center">'
                    '<span style="color:%s; font-size:small;">%s</span></td></tr></table>'
                    % (self.SYS_FG, html_escape(text)))
        body = self._md_to_html(text if role == "assistant" else html_escape(text))
        user = role == "user"
        bg, who = (self.USER_BG, "You") if user else (self.BOT_BG, "FreeGAD")
        bubble = ('<table width="100%%" cellpadding="8" cellspacing="0" bgcolor="%s" style="color:#f2f2f2;">'
                  '<tr><td><span style="font-size:small; color:#c9ced6;"><b>%s</b></span></td></tr>'
                  '<tr><td>%s</td></tr></table>' % (bg, who, body))
        spacer = '<td width="18%"></td>'
        cells = (spacer + "<td>" + bubble + "</td>") if user else ("<td>" + bubble + "</td>" + spacer)
        return '<table width="100%%" cellpadding="0" cellspacing="0"><tr>%s</tr></table><p></p>' % cells

    def render(self):
        parts = [self._bubble(r, t) for r, t in self.transcript]
        if self.current is not None:
            parts.append(self._bubble("assistant", self.current or "…"))
        self.view.setHtml("".join(parts))
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QtCore.QEvent.KeyPress:
            if ev.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and \
                    ev.modifiers() & QtCore.Qt.ControlModifier:
                self.on_send()
                return True
        return super().eventFilter(obj, ev)

    # -- image attachments
    def add_image(self, img):
        enc = encode_image(img)
        if enc is None:
            self.append_system("Could not read the pasted image.")
            return
        btn = QtWidgets.QToolButton()
        btn.setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(
            img.scaled(44, 44, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))))
        btn.setIconSize(QtCore.QSize(44, 44))
        btn.setToolTip("%d×%d image - click to remove" % (img.width(), img.height()))
        entry = (enc[0], enc[1], btn)
        btn.clicked.connect(lambda: self.remove_image(entry))
        self.pending_images.append(entry)
        self.attach_lay.insertWidget(self.attach_lay.count() - 1, btn)
        self.attach_row.show()

    def remove_image(self, entry):
        if entry in self.pending_images:
            self.pending_images.remove(entry)
        entry[2].deleteLater()
        if not self.pending_images:
            self.attach_row.hide()

    def clear_images(self):
        for e in list(self.pending_images):
            self.remove_image(e)

    # -- actions
    def on_auto_toggled(self, on):
        config_mod.save_settings(auto_approve=on)
        reload_config()

    def on_key(self):
        run_dialog(ApiKeyDialog(self))
        self.show_welcome()

    def on_settings(self):
        run_dialog(SettingsDialog(self))

    def on_memory(self):
        run_dialog(MemoryDialog(self))

    def on_reset(self):
        get_agent().reset(App.ActiveDocument)
        self.transcript.clear()
        self.current = None
        self.refresh_doc_label()
        self.render()
        self.append_system("Conversation reset; the document snapshot will be re-read on the next question. "
                           "(Saved history is kept - clear it from Memory… if needed.)")

    def on_send(self):
        if self.worker is not None and self.worker.isRunning():
            return
        text = self.input.toPlainText().strip()
        images = [(mt, data) for mt, data, _btn in self.pending_images]
        if not text and not images:
            return
        cfg = reload_config()
        if not cfg.has_api_key:
            self.append_system("No API key. Click **Key** to set one.")
            return
        self.input.clear()
        self.clear_images()
        self.refresh_doc_label()
        self.load_history()            # switch transcript if the active document changed
        shown = text
        if images:
            marker = "[%d image%s attached]" % (len(images), "" if len(images) == 1 else "s")
            shown = (text + "\n" + marker) if text else marker
        self.transcript.append(("user", shown))
        if self.history is not None:
            self.history.append("user", shown)
        self.current = ""
        self.render()
        self.send_btn.setEnabled(False)
        self.status.setText("Thinking…")

        self.worker = Worker(get_agent(), App.ActiveDocument, text, self.bridge, images)
        self.worker.text.connect(self.on_text)
        self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished_turn.connect(self.on_done)
        self.worker.start()

    def on_text(self, t):
        if self.current is None:
            self.current = ""
        self.current += t
        self.render()

    def on_failed(self, msg):
        App.Console.PrintError("FreeGAD: " + msg + "\n")
        self.current = (self.current or "") + "\n\n**Error:** " + msg.split("\n")[0]

    def on_done(self):
        if self.current is not None:
            self.transcript.append(("assistant", self.current))
            if self.history is not None:
                self.history.append("assistant", self.current)
            self.current = None
        self.render()
        self.status.setText("")
        self.send_btn.setEnabled(True)
        self.input.setFocus()


def show_chat():
    global _dock
    mw = main_window()
    if _dock is None:
        _dock = ChatDock(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, _dock)
    _dock.show()
    _dock.raise_()
    _dock.refresh_doc_label()
    _dock.load_history()
    _dock.input.setFocus()
    return _dock
