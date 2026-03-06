"""
Vercel Serverless Function: PDF翻訳API
POST /api/translate でPDFを受け取り、翻訳済みPDFを返す
"""
from http.server import BaseHTTPRequestHandler
import json
import base64
from io import BytesIO

# PyMuPDF
import fitz

# Google Translate
from deep_translator import GoogleTranslator


# ========================================
# 翻訳モジュール
# ========================================

def translate_text(text: str, source_lang: str = "en", target_lang: str = "ja") -> str:
    """テキストを翻訳する"""
    if not text or not text.strip():
        return text
    
    try:
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        # Google翻訳は5000文字制限があるため、長いテキストは分割
        if len(text) > 4500:
            return translate_long_text(text, source_lang, target_lang)
        return translator.translate(text)
    except Exception as e:
        print(f"翻訳エラー: {e}")
        return text


def translate_long_text(text: str, source_lang: str = "en", target_lang: str = "ja") -> str:
    """長いテキストを分割して翻訳する"""
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    
    sentences = text.replace(". ", ".|").replace("? ", "?|").replace("! ", "!|").split("|")
    
    translated_parts = []
    current_chunk = ""
    
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < 4500:
            current_chunk += sentence
        else:
            if current_chunk:
                translated_parts.append(translator.translate(current_chunk))
            current_chunk = sentence
    
    if current_chunk:
        translated_parts.append(translator.translate(current_chunk))
    
    return " ".join(translated_parts)


# ========================================
# PDF処理モジュール
# ========================================

def extract_text_blocks(page: fitz.Page) -> list:
    """ページからテキストブロックを座標情報付きで抽出"""
    blocks = []
    
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:  # テキストブロック
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        blocks.append({
                            "text": text,
                            "bbox": span.get("bbox"),
                            "font": span.get("font", ""),
                            "size": span.get("size", 12),
                            "color": span.get("color", 0),
                            "origin": span.get("origin", (0, 0))
                        })
    
    return blocks


def translate_pdf(pdf_bytes: bytes, source_lang: str = "en", target_lang: str = "ja") -> bytes:
    """PDFを翻訳し、レイアウトを保持した新しいPDFを生成"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = extract_text_blocks(page)
        
        # 元のテキストを白で塗りつぶし
        for block in blocks:
            bbox = block["bbox"]
            rect = fitz.Rect(bbox)
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        # 翻訳したテキストを配置
        for block in blocks:
            original_text = block["text"]
            translated_text = translate_text(original_text, source_lang, target_lang)
            
            if translated_text:
                bbox = block["bbox"]
                font_size = block["size"]
                adjusted_size = max(font_size * 0.9, 6)
                
                try:
                    insert_point = fitz.Point(bbox[0], bbox[1] + adjusted_size)
                    page.insert_text(
                        insert_point,
                        translated_text,
                        fontsize=adjusted_size,
                        fontname="helv",
                        color=(0, 0, 0)
                    )
                except Exception as e:
                    print(f"テキスト挿入エラー: {e}")
    
    output_buffer = BytesIO()
    doc.save(output_buffer)
    doc.close()
    
    return output_buffer.getvalue()


def get_pdf_info(pdf_bytes: bytes) -> dict:
    """PDFの情報を取得"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    info = {
        "page_count": len(doc),
        "metadata": doc.metadata,
        "total_text_blocks": 0
    }
    
    for page in doc:
        blocks = extract_text_blocks(page)
        info["total_text_blocks"] += len(blocks)
    
    doc.close()
    return info


# ========================================
# API Handler
# ========================================

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """PDF翻訳リクエストを処理"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            
            pdf_base64 = data.get('pdf')
            if not pdf_base64:
                self._send_error(400, "PDFデータが必要です")
                return
            
            pdf_bytes = base64.b64decode(pdf_base64)
            
            source_lang = data.get('source_lang', 'en')
            target_lang = data.get('target_lang', 'ja')
            
            info = get_pdf_info(pdf_bytes)
            translated_pdf = translate_pdf(pdf_bytes, source_lang, target_lang)
            
            response = {
                "success": True,
                "pdf": base64.b64encode(translated_pdf).decode('utf-8'),
                "info": info
            }
            
            self._send_json(200, response)
            
        except json.JSONDecodeError:
            self._send_error(400, "無効なJSONフォーマットです")
        except Exception as e:
            self._send_error(500, f"翻訳処理中にエラーが発生しました: {str(e)}")
    
    def do_OPTIONS(self):
        """CORS preflight対応"""
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()
    
    def _send_json(self, status_code: int, data: dict):
        """JSONレスポンスを送信"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def _send_error(self, status_code: int, message: str):
        """エラーレスポンスを送信"""
        self._send_json(status_code, {
            "success": False,
            "error": message
        })
    
    def _set_cors_headers(self):
        """CORSヘッダーを設定"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
