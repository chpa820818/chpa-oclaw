"""Top toolbar: Azure multi-account / cloud / subscription switcher.

Supports switching between *all* accounts that have been logged into
`az` (via repeated `az login`). The dropdown lists every distinct user
that owns at least one subscription; selecting a user filters the
subscription dropdown to that user's subs and runs `az account set` to
switch the active context.
"""
from __future__ import annotations

import datetime as _dt
import os as _os
import traceback as _tb

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.az_account import AzCli


_LOG_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))),
    "copilot-temp", "sessions", "az-bar.log",
)


def _log(msg: str):
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            ts = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


class _AzWorker(QThread):
    """Run a blocking az callable off the GUI thread."""

    done = Signal(object, object)  # (result, error_or_None)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
            self.done.emit(result, None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


class AzAccountBar(QWidget):
    """Account / Cloud / Subscription switcher with multi-account support."""

    account_changed = Signal()        # emitted after a successful change
    busy_changed = Signal(str, bool)  # (message, busy?) for status bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.az = AzCli()
        self._suspend_signals = False
        self._all_subs: list[dict] = []
        self._workers: list[_AzWorker] = []
        self._busy_count = 0
        self._refresh_in_progress = False
        self._build_ui()
        self.refresh()

    # --- UI -----------------------------------------------------------

    def _build_ui(self):
        self.setObjectName("AzBar")
        rows = QVBoxLayout(self)
        rows.setContentsMargins(12, 6, 12, 6)
        rows.setSpacing(6)
        layout = QHBoxLayout()
        layout.setSpacing(6)
        rows.addLayout(layout)

        lbl_user = QLabel("👤  Account")
        lbl_user.setObjectName("FieldLabel")
        layout.addWidget(lbl_user)
        self.user_box = QComboBox()
        self.user_box.setMinimumWidth(220)
        self.user_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.user_box.setMinimumContentsLength(18)
        self.user_box.currentIndexChanged.connect(self._on_user_changed)
        layout.addWidget(self.user_box)

        lbl_cloud = QLabel("☁  Cloud")
        lbl_cloud.setObjectName("FieldLabel")
        layout.addWidget(lbl_cloud)
        self.cloud_box = QComboBox()
        self.cloud_box.setMinimumWidth(140)
        self.cloud_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cloud_box.setMinimumContentsLength(14)
        self.cloud_box.currentTextChanged.connect(self._on_cloud_changed)
        layout.addWidget(self.cloud_box)

        lbl_sub = QLabel("🗂  Subscription")
        lbl_sub.setObjectName("FieldLabel")
        layout.addWidget(lbl_sub)
        self.sub_box = QComboBox()
        self.sub_box.setMinimumWidth(280)
        self.sub_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sub_box.setMinimumContentsLength(22)
        self.sub_box.currentIndexChanged.connect(self._on_sub_changed)
        layout.addWidget(self.sub_box, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        rows.addLayout(actions)
        # Inline busy indicator (hidden when idle).
        self.busy_label = QLabel("")
        self.busy_label.setWordWrap(True)
        self.busy_label.setObjectName("BusyLabel")
        self.busy_label.setVisible(False)
        actions.addWidget(self.busy_label)
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)         # indeterminate
        self.busy_bar.setMaximumWidth(80)
        self.busy_bar.setMaximumHeight(6)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setVisible(False)
        actions.addWidget(self.busy_bar)
        actions.addStretch(1)

        self.add_btn = QPushButton("＋  Add Account")
        self.add_btn.setProperty("accent", True)
        self.add_btn.setToolTip("Sign in to another account without signing out existing accounts")
        self.add_btn.clicked.connect(self._on_add_account)
        actions.addWidget(self.add_btn)

        self.logout_btn = QPushButton("Sign Out")
        self.logout_btn.setProperty("danger", True)
        self.logout_btn.setToolTip("Sign out only the selected account")
        self.logout_btn.clicked.connect(self._on_logout_current)
        actions.addWidget(self.logout_btn)

        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_btn)

    # --- public --------------------------------------------------------

    def refresh(self):
        if not self.az.available:
            self.user_box.clear()
            self.user_box.addItem("(Azure CLI not installed)")
            for w in (self.cloud_box, self.sub_box, self.add_btn,
                      self.logout_btn, self.user_box):
                w.setEnabled(False)
            return
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        self._begin_busy("Loading Azure accounts and subscriptions…")

        def work():
            return {
                "clouds": self.az.list_clouds(),
                "current_cloud": self.az.current_cloud(),
                "account": self.az.current_account(),
                "subs": self.az.list_subscriptions(),
            }

        def on_done(data, err):
            self._refresh_in_progress = False
            self._end_busy()
            if err is not None:
                QMessageBox.warning(self, "Refresh Failed", str(err))
                return
            self._apply_refresh_data(data)

        self._run_async(work, on_done)

    def _apply_refresh_data(self, data: dict):
        self._suspend_signals = True
        try:
            clouds = data["clouds"]
            current_cloud = data["current_cloud"]
            self.cloud_box.clear()
            self.cloud_box.addItems(clouds)
            if current_cloud and current_cloud in clouds:
                self.cloud_box.setCurrentText(current_cloud)

            account = data["account"]
            self._all_subs = data["subs"] or []

            users = sorted({s.get("user") or "?" for s in self._all_subs
                            if s.get("user")})
            self.user_box.clear()
            if not users:
                self.user_box.addItem("(not signed in)")
                self.user_box.setEnabled(False)
                self.sub_box.clear()
                self.sub_box.setEnabled(False)
                return

            self.user_box.setEnabled(True)
            for u in users:
                count = sum(1 for s in self._all_subs if s.get("user") == u)
                self.user_box.addItem(f"{u}  ({count} subscriptions)", u)

            cur_user = account.user if account else users[0]
            idx = next(
                (i for i in range(self.user_box.count())
                 if self.user_box.itemData(i) == cur_user),
                0,
            )
            self.user_box.setCurrentIndex(idx)
            self._populate_subs_for_user(
                self.user_box.itemData(idx),
                preselect_id=account.subscription_id if account else None,
            )
        finally:
            self._suspend_signals = False

    # --- busy / async helpers -----------------------------------------

    def _begin_busy(self, msg: str):
        self._busy_count += 1
        self.busy_label.setText(f"⏳ {msg}")
        self.busy_label.setVisible(True)
        self.busy_bar.setVisible(True)
        # Disable interactive controls during work.
        for w in (self.user_box, self.cloud_box, self.sub_box,
                  self.add_btn, self.logout_btn, self.refresh_btn):
            w.setEnabled(False)
        self.busy_changed.emit(msg, True)

    def _end_busy(self):
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.busy_label.setVisible(False)
            self.busy_bar.setVisible(False)
            for w in (self.user_box, self.cloud_box, self.sub_box,
                      self.add_btn, self.logout_btn, self.refresh_btn):
                w.setEnabled(True)
            self.busy_changed.emit("", False)

    def _run_async(self, fn, on_done):
        worker = _AzWorker(fn, parent=self)
        self._workers.append(worker)

        def _cleanup(result, err):
            try:
                on_done(result, err)
            finally:
                if worker in self._workers:
                    self._workers.remove(worker)
                worker.deleteLater()

        worker.done.connect(_cleanup)
        worker.start()

    # --- helpers -------------------------------------------------------

    def _populate_subs_for_user(self, user: str | None,
                                preselect_id: str | None = None):
        self.sub_box.clear()
        if not user:
            self.sub_box.setEnabled(False)
            return
        self.sub_box.setEnabled(True)
        subs = [s for s in self._all_subs if s.get("user") == user]
        subs.sort(key=lambda s: (s.get("name") or "").lower())
        target_idx = 0
        for i, s in enumerate(subs):
            label = s.get("name") or "?"
            if s.get("state") and s["state"] != "Enabled":
                label += f"  [{s['state']}]"
            self.sub_box.addItem(label, s.get("id"))
            self.sub_box.setItemData(
                i,
                f"User: {s.get('user')}\nId: {s.get('id')}\n"
                f"Tenant: {s.get('tenantId')}\nCloud: {s.get('cloud')}",
                Qt.ToolTipRole,
            )
            if preselect_id and s.get("id") == preselect_id:
                target_idx = i
        if subs:
            self.sub_box.setCurrentIndex(target_idx)

    # --- event handlers ------------------------------------------------

    def _on_user_changed(self, idx: int):
        if self._suspend_signals or idx < 0:
            return
        user = self.user_box.itemData(idx)
        cur = self.az.current_account()
        keep_id = (cur.subscription_id
                   if cur and cur.user == user else None)
        self._suspend_signals = True
        try:
            self._populate_subs_for_user(user, preselect_id=keep_id)
        finally:
            self._suspend_signals = False
        new_sub_id = self.sub_box.currentData()
        if new_sub_id and (not cur or cur.subscription_id != new_sub_id):
            self._async_set_subscription(new_sub_id, expected_user=user)

    def _on_sub_changed(self, idx: int):
        if self._suspend_signals or idx < 0:
            return
        sub_id = self.sub_box.itemData(idx)
        if not sub_id:
            return
        user = self.user_box.itemData(self.user_box.currentIndex())
        self._async_set_subscription(sub_id, expected_user=user)

    def _async_set_subscription(self, sub_id: str, expected_user: str | None):
        self._begin_busy(f"Switching subscription → {sub_id[:8]}…")

        def on_done(_res, err):
            self._end_busy()
            if err is not None:
                QMessageBox.critical(self, "Subscription Switch Failed", str(err))
                self.refresh()
                return
            self.refresh()
            self.account_changed.emit()

        self._run_async(
            lambda: self._set_subscription_verified(sub_id, expected_user),
            on_done,
        )

    def _set_subscription_verified(
        self,
        sub_id: str,
        expected_user: str | None,
    ):
        """Switch subscription, then return the actual Azure CLI context."""
        self.az.set_subscription(sub_id)
        actual = self.az.current_account()
        if actual is None:
            raise RuntimeError("Could not read the Azure CLI context after switching.")
        if actual.subscription_id.lower() != sub_id.lower():
            raise RuntimeError(
                "The Azure CLI subscription does not match the requested subscription.\n\n"
                f"Requested subscription: {sub_id}\n"
                f"Actual subscription: {actual.subscription_id}\n"
                f"Actual account: {actual.user}"
            )
        if expected_user and actual.user.lower() != expected_user.lower():
            raise RuntimeError(
                "The Azure CLI account does not match the requested account.\n\n"
                f"Requested account: {expected_user}\n"
                f"Actual account: {actual.user}\n"
                f"Subscription: {actual.subscription_name} ({actual.subscription_id})\n\n"
                "Multiple accounts may have access to this subscription, and Azure CLI "
                "selected different credentials. Use Add Account to sign in to the "
                "intended account, or sign out unused accounts and try again."
            )
        return actual

    def _on_cloud_changed(self, name: str):
        if self._suspend_signals or not name:
            return
        current = self.az.current_cloud()
        if name == current:
            return
        confirm = QMessageBox.question(
            self,
            "Switch Cloud",
            f"Switching to {name} usually requires signing in again. Continue?",
        )
        if confirm != QMessageBox.Yes:
            self._suspend_signals = True
            try:
                self.cloud_box.setCurrentText(current)
            finally:
                self._suspend_signals = False
            return

        self._begin_busy(f"Switching cloud → {name}…")

        def on_done(_res, err):
            self._end_busy()
            if err is not None:
                QMessageBox.critical(self, "Cloud Switch Failed", str(err))
                return
            self._spawn_login_and_refresh(tenant=None)

        self._run_async(lambda: self.az.set_cloud(name), on_done)

    def _on_add_account(self):
        _log("_on_add_account: clicked")
        text, ok = QInputDialog.getText(
            self,
            "Add Account",
            "Enter a username (user@domain), or leave blank for default sign-in:\n"
            "Example: chpa@microsoft.com or chpa@mcpod.partner.onmschina.cn\n"
            "The domain after @ is passed to az login as the tenant.",
        )
        _log(f"_on_add_account: dialog ok={ok} text='{text}'")
        if not ok:
            return
        text = text.strip()
        tenant: str | None = None
        if text:
            if "@" in text:
                tenant = text.split("@", 1)[1].strip().lower()
                if not tenant:
                    QMessageBox.warning(
                        self, "Invalid Format", f"Could not extract a domain from '{text}'.",
                    )
                    return
            else:
                # treat the whole input as tenant (GUID or domain)
                tenant = text
            _log(f"  resolved tenant='{tenant}' from input='{text}'")
        self._spawn_login_and_refresh(tenant=tenant)

    def _on_logout_current(self):
        # Use the account currently selected in the dropdown — not
        # `az.current_account()`, which can return None when the active
        # subscription doesn't match any visible account (stale state).
        user = self.user_box.itemData(self.user_box.currentIndex())
        if not user:
            cur = self.az.current_account()
            user = cur.user if cur else None
        if not user:
            QMessageBox.information(
                self, "Sign Out",
                "No account is available to sign out. Click Refresh or Add Account first.",
            )
            return
        confirm = QMessageBox.question(
            self,
            "Sign Out",
            f"Sign out {user}?\nOther signed-in accounts will not be affected.",
        )
        if confirm != QMessageBox.Yes:
            return

        self._begin_busy(f"Signing out {user}…")

        def on_done(_res, err):
            self._end_busy()
            if err is not None:
                QMessageBox.critical(self, "Sign-Out Failed", str(err))
                return
            self.account_changed.emit()
            self.refresh()

        self._run_async(
            lambda: self.az._run(["logout", "--username", user]),
            on_done,
        )

    def _spawn_login_and_refresh(self, tenant: str | None):
        _log(f"_spawn_login_and_refresh: tenant={tenant}")
        try:
            popen = self.az.spawn_login(tenant=tenant)
            _log(f"  spawn_login returned popen pid={popen.pid}")
        except Exception as e:
            _log(f"  spawn_login EXCEPTION: {e}\n{_tb.format_exc()}")
            QMessageBox.critical(self, "Sign-In Failed", str(e))
            return
        msg = (f"Signing in… (tenant={tenant})" if tenant
               else "Signing in… Complete sign-in in the browser or terminal")
        self._begin_busy(msg)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_count = 0

        def _check():
            self._poll_count += 1
            rc = popen.poll()
            if self._poll_count <= 3 or self._poll_count % 10 == 0:
                _log(f"  poll #{self._poll_count}: rc={rc}")
            if rc is None:
                return
            _log(f"  popen exited rc={rc}; refreshing")
            self._poll_timer.stop()
            self._end_busy()
            self.refresh()
            self.account_changed.emit()

        self._poll_timer.timeout.connect(_check)
        self._poll_timer.start()
