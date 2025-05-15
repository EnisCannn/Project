import sys
import os
import urllib.parse
import re
import sqlite3
from datetime import datetime
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.QtCore import *
import fitz  # PyMuPDF
import docx
import google.generativeai as genai
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter

# Google Gemini API'yi başlat
API_KEY = "AIzaSyDza58k0OuIKdoOrKT2fOyfBajUvu4uZ00"
genai.configure(api_key=API_KEY)


class DatabaseManager:
    def __init__(self, db_name="chat_history.db"):
        self.db_name = db_name
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._initialize_database()

    def _initialize_database(self):
        cursor = self.conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            file_path TEXT,
            file_content TEXT,
            created_at TEXT NOT NULL
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
        """)
        cursor.execute("PRAGMA table_info(conversations)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'file_content' not in columns:
            cursor.execute("ALTER TABLE conversations ADD COLUMN file_content TEXT")
        self.conn.commit()

    def create_conversation(self, title, file_path=None, file_content=None):
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO conversations (title, file_path, file_content, created_at)
        VALUES (?, ?, ?, ?)
        """, (title, file_path, file_content, datetime.now().isoformat()))
        self.conn.commit()
        return cursor.lastrowid

    def add_message(self, conversation_id, sender, content):
        cursor = self.conn.cursor()
        cursor.execute("""
        INSERT INTO messages (conversation_id, sender, content, timestamp)
        VALUES (?, ?, ?, ?)
        """, (conversation_id, sender, content, datetime.now().isoformat()))
        self.conn.commit()

    def get_conversations(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, title FROM conversations ORDER BY created_at DESC")
        return cursor.fetchall()

    def get_messages(self, conversation_id):
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT sender, content, timestamp FROM messages 
        WHERE conversation_id = ? 
        ORDER BY timestamp ASC
        """, (conversation_id,))
        return cursor.fetchall()

    def get_conversation_details(self, conversation_id):
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT file_path, file_content FROM conversations WHERE id = ?
        """, (conversation_id,))
        return cursor.fetchone()

    def delete_conversation(self, conversation_id):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def close(self):
        self.conn.close()


def gemini_generate(prompt):
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(prompt)
        return response.text.strip() if response else "Bilinmeyen Konu"
    except Exception as e:
        print(f"API Hatası: {e}")
        return "Bilinmeyen Konu"


class FileProcessor:
    @staticmethod
    def extract_text(file_path):
        text = ""
        try:
            if file_path.endswith('.pdf'):
                with fitz.open(file_path) as doc:
                    for page in doc:
                        text += page.get_text("text") + "\n"
            elif file_path.endswith('.docx'):
                doc = docx.Document(file_path)
                for para in doc.paragraphs:
                    text += para.text + "\n"
        except Exception as e:
            print(f"Dosya Okuma Hatası: {e}")
        return text

    @staticmethod
    def preprocess_text(text):
        # 1) **bold** → <b>…</b>
        text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
        # 2) *italik* → <i>…</i>
        text = re.sub(
            r'(?<!\*)\*(?!\*)(.*?)\*(?!\*)',
            r'<i>\1</i>',
            text,
            flags=re.DOTALL
        )
        return text

    @staticmethod
    def summarize_text(text):
        processed_text = FileProcessor.preprocess_text(text)
        prompt = (f"Bu metni özetle. Metindeki başlıkları ve önemli vurguları koru:\n{processed_text[:4000]}")
        return gemini_generate(prompt)

    @staticmethod
    def generate_title(text):
        prompt = f"Bu metinle ilgili en uygun kısa başlığı öner, sadece başlığı yaz:\n{text[:500]}"
        return gemini_generate(prompt)

    @staticmethod
    def answer_question(text, chat_history, question):
        chat_context = "\n".join(chat_history[-5:])
        prompt = (
            f"Önceki sohbet:\n{chat_context}\n\n"
            f"Metin:\n{FileProcessor.preprocess_text(text[:4000])}\n\n"
            f"Soru: {question}\n\n"
            f"Kod bloklarını olduğu gibi koru ve ``` ile göster."
        )
        return gemini_generate(prompt)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.file_text = ""
        self.db = DatabaseManager()
        self.current_conversation_id = None
        self.initUI()
        self.load_conversation_list()

    def initUI(self):
        # Pencere ayarları
        self.setGeometry(50, 50, 1200, 640)
        self.setWindowTitle('RwAi')
        self.setWindowIcon(QIcon("C:\\Users\\enisa\\Desktop\\icon\\artificial-intelligence.png"))
        self.setStyleSheet("background-color: white;")
        self.theme_color = "#D8BFD8"

        # Soldaki sohbet geçmişi listesi
        self.history_list = QListWidget(self)
        self.history_list.setGeometry(10, 10, 200, 620)
        self.history_list.itemClicked.connect(self.load_conversation)

        # “Dosya Yükle” butonu
        self.button1 = QPushButton('Dosya Yükle', self)
        self.button1.setGeometry(220, 10, 150, 40)
        self.button1.setStyleSheet(f"background-color: {self.theme_color}; border-radius: 10px;")
        self.button1.clicked.connect(self.load_file)

        # Chat alanı
        self.chat_area = QTextBrowser(self)
        self.chat_area.setGeometry(220, 60, 960, 400)
        # copy:// linklerini harici açma, kendi handler’ına bırak
        self.chat_area.setOpenExternalLinks(False)
        self.chat_area.setOpenLinks(False)
        self.chat_area.anchorClicked.connect(self.handle_anchor_click)
        # Sadece genel div stili, <pre> stili inline olarak kod bloğunda verilecek
        self.chat_area.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #ccc;
                padding: 10px;
                background-color: white;
                font-size: 9pt;
                font-style: normal;

            }
            QTextBrowser div {
                margin: 10px 0;
                font-size: 9pt;
                font-style: normal;
            }
        """)

        # Kullanıcının soru gireceği kutu
        self.question_input = QLineEdit(self)
        self.question_input.setGeometry(220, 480, 800, 40)
        self.question_input.setPlaceholderText("Sorunuzu buraya yazın...")
        self.question_input.setStyleSheet("border: 1px solid #ccc; padding: 5px;")
        self.question_input.returnPressed.connect(self.ask_question)

        # “Gönder” butonu
        self.ask_button = QPushButton("Gönder", self)
        self.ask_button.setGeometry(1030, 480, 150, 40)
        self.ask_button.setStyleSheet(f"background-color: {self.theme_color}; border-radius: 10px;")
        self.ask_button.clicked.connect(self.ask_question)

        # “Sohbeti Sil” butonu
        self.delete_button = QPushButton("Sohbeti Sil", self)
        self.delete_button.setGeometry(10, 580, 200, 40)
        self.delete_button.setStyleSheet("background-color: #ff6b6b; color: white; border-radius: 10px;")
        self.delete_button.clicked.connect(self.delete_conversation)

    def handle_anchor_click(self, url):
        if url.scheme() == "copy":
            # URL path’ini percent‐decoded olarak al
            text = urllib.parse.unquote(url.path())
            QApplication.clipboard().setText(text)


    def format_code_with_copy_button(self, text):
        # Kod bloklarını (```lang\n…```) Pygments + kopyala butonu ile sar
        return re.sub(
            r'```(\w+)?\n(.*?)```',
            self._format_code_block,
            text,
            flags=re.DOTALL
        )

    def _format_code_block(self, match):
        language = match.group(1) or "text"
        code = match.group(2).strip()

        # Pygments ile renklendir
        try:
            lexer = get_lexer_by_name(language, stripall=True)
        except:
            lexer = get_lexer_by_name('text', stripall=True)
        formatter = HtmlFormatter(style="monokai", noclasses=True, nowrap=True, prestyles="margin:0; padding:0;")
        highlighted_code = highlight(code, lexer, formatter)

        # İçeriği percent‐encode et
        escaped_code = urllib.parse.quote(code)

        return (
            '<div style="position: relative;">'
            # copy: scheme kullanarak path içine encode edilmiş kodu koy
            f'<a href="copy:{escaped_code}" style="text-decoration:none; color:white;">'
            '<span style="position: absolute; right: 5px; top: 5px; '
            'background: #555; color: white; border-radius: 3px; '
            'padding: 2px 5px; cursor: pointer; font-size: 12px;">Kopyala</span>'
            '</a>'
            f'<pre style="background-color: #2d2d2d; color: #f8f8f2; padding: 10px; '
            'border-radius: 5px; font-family: monospace; white-space: pre-wrap; '
            f'margin: 10px 0;">{highlighted_code}</pre>'
            '</div>'
        )

    def load_conversation_list(self):
        self.history_list.clear()
        for conv_id, title in self.db.get_conversations():
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, conv_id)
            self.history_list.addItem(item)

    def load_file(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Dosya Seç", "",
            "PDF Files (*.pdf);;Word Files (*.docx)",
            options=options
        )
        if not file_path:
            return
        try:
            file_content = FileProcessor.extract_text(file_path)
            title = FileProcessor.generate_title(file_content)
            summary = FileProcessor.summarize_text(file_content)
            self.current_conversation_id = self.db.create_conversation(
                title, file_path, file_content
            )
            self.db.add_message(self.current_conversation_id, "system", summary)
            self.load_conversation_list()
            self.chat_area.clear()
            self.display_message("Sistem", summary)
            self.file_text = file_content
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Dosya yükleme hatası: {e}")

    def display_message(self, sender, content):
        # 1) Markdown dönüşümü
        html = FileProcessor.preprocess_text(content)
        # 2) Kod bloklarını renklendir + kopyala butonu ekle
        formatted = self.format_code_with_copy_button(html)

        if sender == "Siz":
            # Sadece sizin mesajlarınız baloncuk içinde
            bubble_bg = "#f0f0f0"
            border_color = "#ccc"
            bubble_html = f'''
            <table align="right" cellspacing="0" cellpadding="8"
                   style="
                     display: inline-block;
                     background-color: {bubble_bg};
                     border: 1px solid {border_color};
                     max-width: 80%;
                     word-wrap: break-word;
                     margin: 20px 0;
                   ">
              <tr>
                <td><b>{sender}:</b> {formatted}</td>
              </tr>
            </table>
            '''
            self.chat_area.append(bubble_html)
        else:
            # Sistem veya Asistan mesajları normal div içinde
            # (isterseniz buraya kendi stillerinizi ekleyebilirsiniz)
            self.chat_area.append(
                f'<div style="margin:10px 0; padding:8px;">'
                f'<b>{sender}:</b> {formatted}'
                '</div>'
            )

    def ask_question(self):
        if not self.current_conversation_id:
            QMessageBox.warning(self, "Uyarı", "Önce bir dosya yükleyin veya konuşma seçin!")
            return
        question = self.question_input.text().strip()
        if not question:
            return
        self.db.add_message(self.current_conversation_id, "user", question)
        self.display_message("Siz", question)
        self.question_input.clear()

        try:
            messages = [f"{s}: {c}" for s, c, _ in self.db.get_messages(self.current_conversation_id)]
            answer = FileProcessor.answer_question(self.file_text, messages, question)
            self.db.add_message(self.current_conversation_id, "assistant", answer)
            self.display_message("Asistan", answer)
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Soru cevaplama hatası: {e}")

    def load_conversation(self, item):
        conv_id = item.data(Qt.UserRole)
        self.current_conversation_id = conv_id
        details = self.db.get_conversation_details(conv_id)
        self.file_text = details[1] or ""
        self.chat_area.clear()
        for sender, content, _ in self.db.get_messages(conv_id):
            tag = "Asistan" if sender=="assistant" else ("Siz" if sender=="user" else "Sistem")
            self.display_message(tag, content)

    def delete_conversation(self):
        sel = self.history_list.currentItem()
        if not sel:
            QMessageBox.warning(self, "Uyarı", "Lütfen silmek istediğiniz sohbeti seçin!")
            return
        conv_id = sel.data(Qt.UserRole)
        if QMessageBox.question(self, "Onay",
                                "Bu sohbeti silmek istediğinize emin misiniz?",
                                QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            if self.db.delete_conversation(conv_id):
                self.history_list.takeItem(self.history_list.row(sel))
                if self.current_conversation_id == conv_id:
                    self.chat_area.clear()
                    self.current_conversation_id = None
                    self.file_text = ""

    def closeEvent(self, event):
        self.db.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    sys.exit(app.exec_())
