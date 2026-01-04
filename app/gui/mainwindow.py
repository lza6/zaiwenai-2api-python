import sys
import os
import asyncio
from pathlib import Path
from collections import deque
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QTextEdit, QListWidget,
    QFrame, QGroupBox, QMessageBox, QApplication,
    QListWidgetItem, QProgressBar, QComboBox,
    QTabWidget, QMenu, QInputDialog
)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QAction

from app.gui.themes import THEMES
from app.gui.worker import ServerWorker
import aiosqlite
from app.core.database import DB_PATH

class StatCard(QFrame):
    def __init__(self, icon: str, title: str, value: str = "0", color: str = "#3b82f6", parent=None):
        super().__init__(parent)
        self.icon = icon
        self.color = color
        self._setup_ui(title, value)
    
    def _setup_ui(self, title: str, value: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(16, 16, 16, 16)
        
        header = QHBoxLayout()
        self.icon_label = QLabel(self.icon)
        self.icon_label.setStyleSheet(f"font-size: 18px;")
        header.addWidget(self.icon_label)
        
        self.title_label = QLabel(title)
        header.addWidget(self.title_label)
        header.addStretch()
        layout.addLayout(header)
        
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color: {self.color}; font-size: 28px; font-weight: bold;")
        layout.addWidget(self.value_label)
    
    def set_value(self, value):
        self.value_label.setText(str(value))
    
    def apply_theme(self, theme: dict):
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {theme['bg_secondary']};
                border-radius: 12px;
                border: 1px solid {theme['border']};
            }}
        """)
        self.title_label.setStyleSheet(f"color: {theme['text_secondary']}; font-size: 13px;")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_theme_name = "dark"
        self.theme = THEMES[self.current_theme_name]
        self.setWindowTitle("🚀 Zaiwen 2API Manager v2.0")
        self.setMinimumSize(1000, 700)
        self.resize(1100, 800)
        
        self.server_worker = None
        self.all_logs = []
        
        self._setup_ui()
        self._apply_theme()
        
        self._refresh_tokens()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("🚀 Zaiwen 2API Manager")
        title_label.setObjectName("main_title")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        self.theme_combo = QComboBox()
        for key, value in THEMES.items():
            self.theme_combo.addItem(value["name"], key)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        header_layout.addWidget(QLabel("主题:"))
        header_layout.addWidget(self.theme_combo)
        main_layout.addLayout(header_layout)
        
        # Stats
        stats_layout = QHBoxLayout()
        self.token_card = StatCard("🔑", "API Tokens", "0", "#3b82f6")
        self.status_card = StatCard("📡", "服务状态", "已停止", "#f59e0b")
        self.req_card = StatCard("⚡", "请求总数", "N/A", "#8b5cf6") 
        
        stats_layout.addWidget(self.token_card)
        stats_layout.addWidget(self.status_card)
        stats_layout.addWidget(self.req_card)
        main_layout.addLayout(stats_layout)
        
        # Control Panel
        control_group = QGroupBox("⚙️ 服务控制")
        control_group.setObjectName("control_group")
        control_layout = QHBoxLayout(control_group)
        
        self.start_btn = QPushButton("▶ 启动服务")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self._on_start_server)
        
        self.stop_btn = QPushButton("⏹ 停止服务")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_server)
        
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addStretch()
        main_layout.addWidget(control_group)

        # Tabs
        self.tab_widget = QTabWidget()
        
        # Log Tab
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        self.tab_widget.addTab(log_tab, "📋 系统日志")
        
        # Token Tab
        token_tab = QWidget()
        token_layout = QVBoxLayout(token_tab)
        self.token_list = QListWidget()
        self.token_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.token_list.customContextMenuRequested.connect(self._show_token_context_menu)
        token_layout.addWidget(self.token_list)
        
        btn_layout = QHBoxLayout()
        add_token_btn = QPushButton("➕ 添加 Token")
        add_token_btn.clicked.connect(self._add_token_dialog)
        
        import_curl_btn = QPushButton("📥 导入 cURL")
        import_curl_btn.clicked.connect(self._import_from_curl)
        
        refresh_btn = QPushButton("🔄 刷新列表")
        refresh_btn.clicked.connect(self._refresh_tokens)
        
        btn_layout.addWidget(add_token_btn)
        btn_layout.addWidget(import_curl_btn)
        btn_layout.addWidget(refresh_btn)
        token_layout.addLayout(btn_layout)
        
        self.tab_widget.addTab(token_tab, "🔑 账号管理")
        
        main_layout.addWidget(self.tab_widget)
        self._add_log("欢迎使用 Zaiwen 2API Manager", "info")

    def _get_stylesheet(self) -> str:
        t = self.theme
        return f"""
            QMainWindow {{ background-color: {t['bg_primary']}; }}
            QWidget {{ color: {t['text_primary']}; font-family: 'Microsoft YaHei', sans-serif; }}
            #main_title {{ font-size: 26px; font-weight: bold; color: {t['text_primary']}; }}
            QGroupBox {{ border: 1px solid {t['border']}; border-radius: 10px; padding-top: 12px; background-color: {t['bg_secondary']}; }}
            QPushButton {{ background-color: {t['accent_blue']}; color: white; border-radius: 8px; padding: 10px 20px; font-weight: bold; }}
            QPushButton:disabled {{ background-color: {t['bg_tertiary']}; color: {t['text_secondary']}; }}
            #startBtn {{ background-color: {t['accent_green']}; }}
            #stopBtn {{ background-color: {t['accent_red']}; }}
            QTextEdit {{ background-color: {t['bg_primary']}; border: 1px solid {t['border']}; border-radius: 8px; font-family: 'Consolas'; }}
            QListWidget {{ background-color: {t['bg_primary']}; border: 1px solid {t['border']}; border-radius: 8px; }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; background-color: {t['bg_secondary']}; }}
            QTabBar::tab {{ background-color: {t['bg_tertiary']}; padding: 10px 20px; }}
            QTabBar::tab:selected {{ background-color: {t['bg_secondary']}; }}
        """

    def _apply_theme(self):
        self.setStyleSheet(self._get_stylesheet())
        self.token_card.apply_theme(self.theme)
        self.status_card.apply_theme(self.theme)
        self.req_card.apply_theme(self.theme)

    def _on_theme_changed(self, index):
        key = self.theme_combo.currentData()
        self.theme = THEMES[key]
        self._apply_theme()

    def _add_log(self, message: str, level: str = "info"):
        colors = {"info": self.theme["accent_blue"], "success": self.theme["accent_green"], "error": self.theme["accent_red"], "warning": self.theme["accent_yellow"]}
        color = colors.get(level, self.theme["text_primary"])
        html = f'<span style="color: {color};">[{level.upper()}] {message}</span><br>'
        self.log_text.insertHtml(html)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def _on_start_server(self):
        if self.server_worker and self.server_worker.isRunning():
            return
        
        self.server_worker = ServerWorker()
        self.server_worker.log_signal.connect(self._add_log)
        self.server_worker.status_signal.connect(self._update_server_status)
        self.server_worker.start()
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _on_stop_server(self):
        if self.server_worker:
            self.server_worker.stop()
            self.server_worker.wait()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
    
    def _update_server_status(self, is_running):
        if is_running:
            self.status_card.set_value("运行中")
            self.status_card.value_label.setStyleSheet(f"color: {self.theme['accent_green']}; font-size: 28px; font-weight: bold;")
        else:
            self.status_card.set_value("已停止")
            self.status_card.value_label.setStyleSheet(f"color: {self.theme['accent_red']}; font-size: 28px; font-weight: bold;")

    def _refresh_tokens(self):
        try:
            import sqlite3
            import os
            
            # 确保 data 目录存在
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # 确保表存在
            c.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'active',
                    last_used_at REAL DEFAULT 0,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)
            conn.commit()
            
            # 自动从 tokens.txt 导入
            tokens_file = "data/tokens.txt"
            imported_count = 0
            if os.path.exists(tokens_file):
                try:
                    with open(tokens_file, "r", encoding="utf-8") as f:
                        lines = [line.strip() for line in f if line.strip()]
                    for token in lines:
                        try:
                            c.execute("INSERT OR IGNORE INTO accounts (token) VALUES (?)", (token,))
                            if c.rowcount > 0:
                                imported_count += 1
                        except:
                            pass
                    conn.commit()
                    if imported_count > 0:
                        self._add_log(f"从 tokens.txt 导入了 {imported_count} 个新 Token", "success")
                except Exception as e:
                    self._add_log(f"读取 tokens.txt 失败: {e}", "warning")
            
            c.execute("SELECT token, status FROM accounts")
            rows = c.fetchall()
            conn.close()
            
            self.token_list.clear()
            for token, status in rows:
                display = f"{token[:15]}... | {status}"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, token)
                if status == 'active':
                     item.setForeground(QColor(self.theme["accent_green"]))
                else:
                     item.setForeground(QColor(self.theme["accent_red"]))
                self.token_list.addItem(item)
            
            self.token_card.set_value(len(rows))
            self._add_log(f"已刷新 Token 列表: {len(rows)} 个", "info")
        except Exception as e:
            self._add_log(f"刷新 Token 失败: {str(e)}", "error")

    def _add_token_dialog(self):
        text, ok = QInputDialog.getText(self, "添加 Token", "请输入 Token:")
        if ok and text:
            self._insert_token(text.strip())

    def _import_from_curl(self):
        text, ok = QInputDialog.getMultiLineText(self, "导入 cURL", "请粘贴 cURL 命令 (自动提取 Token):")
        if ok and text:
            import re
            # Try to match token header:  -H 'token: ...' or -H "token: ..." or token: ...
            # Regex for token: value
            # Matches: token: value, "token": "value", 'token': 'value'
            # Note: The user said Zaiwen uses a header named `token`.
            
            # Simple heuristic regex
            patterns = [
                r"token[:=]\s*['\"]?([a-zA-Z0-9\-\._]+)['\"]?",
                r"['\"]token['\"]\s*[:=]\s*['\"]?([a-zA-Z0-9\-\._]+)['\"]?"
            ]
            
            found = None
            for p in patterns:
                match = re.search(p, text, re.IGNORECASE)
                if match:
                    found = match.group(1)
                    break
            
            if found:
                self._insert_token(found)
                self._add_log(f"成功从 cURL 提取 Token: {found[:10]}...", "success")
            else:
                self._add_log("未从 cURL 中找到 Token，请检查格式。", "warning")

    def _insert_token(self, token):
        if not token: return
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("INSERT OR IGNORE INTO accounts (token) VALUES (?)", (token,))
            conn.commit()
            self._add_log(f"Token 已添加", "success")
            self._refresh_tokens()
        except Exception as e:
            self._add_log(f"添加失败: {e}", "error")
        finally:
            conn.close()

    def _show_token_context_menu(self, position):
        menu = QMenu()
        copy_action = menu.addAction("📋 复制")
        delete_action = menu.addAction("🗑️ 删除")
        action = menu.exec(self.token_list.mapToGlobal(position))
        
        item = self.token_list.currentItem()
        if not item: return
        token = item.data(Qt.ItemDataRole.UserRole)
        
        if action == copy_action:
            QApplication.clipboard().setText(token)
            self._add_log("Token 已复制", "success")
        elif action == delete_action:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM accounts WHERE token = ?", (token,))
            conn.commit()
            conn.close()
            self._refresh_tokens()
            self._add_log("Token 已删除", "warning")

    def closeEvent(self, event):
        if self.server_worker and self.server_worker.isRunning():
            self.server_worker.stop()
            self.server_worker.wait()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
