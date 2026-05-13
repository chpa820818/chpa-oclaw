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
        self._build_ui()
        self.refresh()

    # --- UI -----------------------------------------------------------

    def _build_ui(self):
        self.setObjectName("AzBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)

        lbl_user = QLabel("👤  账户")
        lbl_user.setObjectName("FieldLabel")
        layout.addWidget(lbl_user)
        self.user_box = QComboBox()
        self.user_box.setMinimumWidth(220)
        self.user_box.currentIndexChanged.connect(self._on_user_changed)
        layout.addWidget(self.user_box)

        lbl_cloud = QLabel("☁  Cloud")
        lbl_cloud.setObjectName("FieldLabel")
        layout.addWidget(lbl_cloud)
        self.cloud_box = QComboBox()
        self.cloud_box.setMinimumWidth(140)
        self.cloud_box.currentTextChanged.connect(self._on_cloud_changed)
        layout.addWidget(self.cloud_box)

        lbl_sub = QLabel("🗂  订阅")
        lbl_sub.setObjectName("FieldLabel")
        layout.addWidget(lbl_sub)
        self.sub_box = QComboBox()
        self.sub_box.setMinimumWidth(280)
        self.sub_box.currentIndexChanged.connect(self._on_sub_changed)
        layout.addWidget(self.sub_box, 1)

        # Inline busy indicator (hidden when idle).
        self.busy_label = QLabel("")
        self.busy_label.setObjectName("BusyLabel")
        self.busy_label.setVisible(False)
        layout.addWidget(self.busy_label)
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)         # indeterminate
        self.busy_bar.setMaximumWidth(80)
        self.busy_bar.setMaximumHeight(6)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setVisible(False)
        layout.addWidget(self.busy_bar)

        self.add_btn = QPushButton("＋  添加账户")
        self.add_btn.setProperty("accent", True)
        self.add_btn.setToolTip("追加登录另一个账户（不会登出现有账户）")
        self.add_btn.clicked.connect(self._on_add_account)
        layout.addWidget(self.add_btn)

        self.logout_btn = QPushButton("登出")
        self.logout_btn.setProperty("danger", True)
        self.logout_btn.setToolTip("仅登出当前选中的账户")
        self.logout_btn.clicked.connect(self._on_logout_current)
        layout.addWidget(self.logout_btn)

        self.refresh_btn = QPushButton("⟳  刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_btn)

    # --- public --------------------------------------------------------

    def refresh(self):
        if not self.az.available:
            self.user_box.clear()
            self.user_box.addItem("(az CLI 未安装)")
            for w in (self.cloud_box, self.sub_box, self.add_btn,
                      self.logout_btn, self.user_box):
                w.setEnabled(False)
            return
        self._begin_busy("正在读取 Azure 账户和订阅…")

        def work():
            return {
                "clouds": self.az.list_clouds(),
                "current_cloud": self.az.current_cloud(),
                "account": self.az.current_account(),
                "subs": self.az.list_subscriptions(),
            }

        def on_done(data, err):
            self._end_busy()
            if err is not None:
                QMessageBox.warning(self, "刷新失败", str(err))
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
                self.user_box.addItem("(未登录)")
                self.user_box.setEnabled(False)
                self.sub_box.clear()
                self.sub_box.setEnabled(False)
                return

            self.user_box.setEnabled(True)
            for u in users:
                count = sum(1 for s in self._all_subs if s.get("user") == u)
                self.user_box.addItem(f"{u}  ({count} 订阅)", u)

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
            self._async_set_subscription(new_sub_id)

    def _on_sub_changed(self, idx: int):
        if self._suspend_signals or idx < 0:
            return
        sub_id = self.sub_box.itemData(idx)
        if not sub_id:
            return
        self._async_set_subscription(sub_id)

    def _async_set_subscription(self, sub_id: str):
        self._begin_busy(f"切换订阅 → {sub_id[:8]}…")

        def on_done(_res, err):
            self._end_busy()
            if err is not None:
                QMessageBox.critical(self, "切换订阅失败", str(err))
                self.refresh()
                return
            self.account_changed.emit()

        self._run_async(lambda: self.az.set_subscription(sub_id), on_done)

    def _on_cloud_changed(self, name: str):
        if self._suspend_signals or not name:
            return
        current = self.az.current_cloud()
        if name == current:
            return
        confirm = QMessageBox.question(
            self,
            "切换 Cloud",
            f"切换到 {name} 后通常需要重新登录。继续？",
        )
        if confirm != QMessageBox.Yes:
            self._suspend_signals = True
            try:
                self.cloud_box.setCurrentText(current)
            finally:
                self._suspend_signals = False
            return

        self._begin_busy(f"切换 Cloud → {name}…")

        def on_done(_res, err):
            self._end_busy()
            if err is not None:
                QMessageBox.critical(self, "切换 Cloud 失败", str(err))
                return
            self._spawn_login_and_refresh(tenant=None)

        self._run_async(lambda: self.az.set_cloud(name), on_done)

    def _on_add_account(self):
        _log("_on_add_account: clicked")
        text, ok = QInputDialog.getText(
            self,
            "添加账户登录",
            "输入用户名（user@domain，留空走默认登录）：\n"
            "例如：chpa@microsoft.com 或 chpa@mcpod.partner.onmschina.cn\n"
            "提示：会自动从 @ 后提取 tenant 域名传给 az login。",
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
                        self, "格式错误", f"无法从 '{text}' 提取域名。",
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
                self, "登出",
                "当前账户列表为空，无法登出。请先点击‘刷新’或‘添加账户’。",
            )
            return
        confirm = QMessageBox.question(
            self,
            "登出账户",
            f"确认登出 {user}？\n（其他已登录账户不受影响）",
        )
        if confirm != QMessageBox.Yes:
            return

        self._begin_busy(f"登出 {user}…")

        def on_done(_res, err):
            self._end_busy()
            if err is not None:
                QMessageBox.critical(self, "登出失败", str(err))
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
            QMessageBox.critical(self, "登录失败", str(e))
            return
        msg = (f"登录中… (tenant={tenant})" if tenant
               else "登录中… 请在弹出的浏览器/终端完成")
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

