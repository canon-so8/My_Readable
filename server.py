"""
ローカル開発用サーバー
FastAPIを使用してPDF翻訳APIを提供
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import base64
from io import BytesIO
import os

# PyMuPDF
import fitz

# Google Translate
from deep_translator import GoogleTranslator

app = FastAPI(title="My Readable API")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranslateRequest(BaseModel):
    pdf: str
    source_lang: str = "en"
    target_lang: str = "ja"


class TranslateResponse(BaseModel):
    success: bool
    pdf: str = None
    info: dict = None
    error: str = None


# ========================================
# 翻訳モジュール（バッチ最適化版 + キャッシュ）
# ========================================

import json
import hashlib

class TranslationCache:
    def __init__(self, cache_file="translation_cache.json"):
        self.cache_file = cache_file
        self.cache = self._load_cache()
    
    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"キャッシュ保存エラー: {e}")
            
    def get(self, text, source, target):
        key = f"{source}_{target}_{text}"
        # ハッシュキーでも良いが、デバッグしやすいようにテキストを含める
        # 長すぎる場合はハッシュ化
        if len(key) > 100:
            key_hash = hashlib.md5(key.encode()).hexdigest()
            return self.cache.get(key_hash)
        return self.cache.get(key)
    
    def set(self, text, source, target, translated_text):
        if not translated_text:
            return
        key = f"{source}_{target}_{text}"
        if len(key) > 100:
            key_hash = hashlib.md5(key.encode()).hexdigest()
            self.cache[key_hash] = translated_text
        else:
            self.cache[key] = translated_text

# グローバルキャッシュインスタンス
translation_cache = TranslationCache()

# ========================================
# 翻訳関数（Google翻訳）
# ========================================

def translate_text(text: str, source_lang: str = "en", target_lang: str = "ja") -> str:
    """Google翻訳でテキストを翻訳する"""
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    if not text or not text.strip():
        return text
    cached = translation_cache.get(text, source_lang, target_lang)
    if cached:
        return cached
    try:
        result = translator.translate(text[:4500])
        if result:
            translation_cache.set(text, source_lang, target_lang, result)
            translation_cache.save_cache()
        return result if result else text
    except Exception as e:
        print(f"翻訳エラー: {e}")
        return text


def translate_batch(texts: list[str], source_lang: str = "en", target_lang: str = "ja") -> list[str]:
    """Google翻訳でバッチ翻訳する"""
    if not texts:
        return []
    results = [""] * len(texts)
    uncached_indices = []
    uncached_texts = []
    for i, text in enumerate(texts):
        if not text or not text.strip():
            results[i] = text
            continue
        cached = translation_cache.get(text, source_lang, target_lang)
        if cached:
            results[i] = cached
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)
    if not uncached_texts:
        return results
    print(f"  翻訳必要数: {len(uncached_texts)} / {len(texts)} (キャッシュヒット: {len(texts) - len(uncached_texts)})")
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    for idx, text in zip(uncached_indices, uncached_texts):
        try:
            result = translator.translate(text[:4500])
            if result:
                results[idx] = result
                translation_cache.set(text, source_lang, target_lang, result)
            else:
                results[idx] = text
        except Exception as e:
            print(f"  翻訳エラー: {e}")
            results[idx] = text
    translation_cache.save_cache()
    return results


# ========================================
# PDF処理モジュール
# ========================================

# ========================================
# PDF処理モジュール
# ========================================

# 日本語フォントパス
FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "ipaexg.ttf")

def register_font():
    """日本語フォントを登録"""
    try:
        if os.path.exists(FONT_PATH):
            return "ipaexg"
        return "china-s"  # フォールバック
    except:
        return "china-s"

def extract_and_merge_blocks(page: fitz.Page) -> list:
    """ページからテキストブロックを抽出し、段落レベルで結合する"""
    
    # 1. PyMuPDFの機能で表領域を検出
    table_rects = []
    try:
        tables = page.find_tables()
        for tab in tables:
            table_rects.append(fitz.Rect(tab.bbox))
    except Exception as e:
        print(f"表検出エラー (無視します): {e}")

    # 2. 描画オブジェクト（線・図形）から表と図の領域を推定
    drawings = page.get_drawings()
    path_rects = []
    horizontal_lines = []
    
    # 全ての描画パスのbboxを収集
    for draw in drawings:
        r = draw["rect"]
        
        # 水平線検出用（高さが小さく幅がある）
        if r.height < 5 and r.width > 50:
            horizontal_lines.append(r)

        # 図形検出用（極小の点はノイズとして無視、ページ全体のような巨大な枠も無視）
        if r.width > 2 and r.height > 2 and r.width < page.rect.width * 0.9:
            path_rects.append(r)

    # --- 水平線による表領域推定 ---
    horizontal_lines.sort(key=lambda r: r.y0)
    processed_lines = set()
    for i in range(len(horizontal_lines)):
        if i in processed_lines:
            continue
        top_line = horizontal_lines[i]
        for j in range(i + 1, len(horizontal_lines)):
            bottom_line = horizontal_lines[j]
            x_overlap = max(0, min(top_line.x1, bottom_line.x1) - max(top_line.x0, bottom_line.x0))
            overlap_ratio = x_overlap / min(top_line.width, bottom_line.width)
            
            if overlap_ratio > 0.8:
                skip_rect = fitz.Rect(
                    min(top_line.x0, bottom_line.x0) - 2,
                    top_line.y0 - 2,
                    max(top_line.x1, bottom_line.x1) + 2,
                    bottom_line.y1 + 2
                )
                table_rects.append(skip_rect)
                break

    # --- 領域の統合（図形クラスタリング） ---
    merged_rects = []
    if path_rects:
        candidates = path_rects[:]
        while candidates:
            current = candidates.pop(0)
            changed = True
            while changed:
                changed = False
                i = 0
                while i < len(candidates):
                    other = candidates[i]
                    expanded = fitz.Rect(current.x0 - 15, current.y0 - 15, current.x1 + 15, current.y1 + 15) # 感度を少し上げる
                    if expanded.intersects(other):
                        current = current | other
                        candidates.pop(i)
                        changed = True
                    else:
                        i += 1
            merged_rects.append(current)

    figure_rects = []
    for r in merged_rects:
        if r.width > 30 and r.height > 40: # 少し高さを条件に追加
            figure_rects.append(r)

    # 3. 画像（Image）領域も取得
    try:
        images = page.get_images()
        for img in images:
            xref = img[0]
            img_rects = page.get_image_rects(xref)
            for lr in img_rects:
                figure_rects.append(lr)
    except:
        pass
    
    # 表・図スキップリストとしてまとめる
    skip_regions = table_rects + figure_rects

    # 4. テキスト情報を取得し、セパレータ等を探す（補助）
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    detailed_blocks = []
    
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            lines = block.get("lines", [])
            if not lines:
                continue
                
            try:
                first_span = lines[0]["spans"][0]
                font_size = first_span["size"]
                font_name = first_span["font"]
                bbox = fitz.Rect(block["bbox"])
                
                full_text = ""
                for line in lines:
                    for span in line["spans"]:
                        full_text += span["text"]
                    full_text += " "
                
                cleaned_text = full_text.strip()
                
                # スキップ判定
                should_skip = False
                
                # 1. スキップ領域（表・図）内にあるか
                center = fitz.Point((bbox.x0 + bbox.x1)/2, (bbox.y0 + bbox.y1)/2)
                in_skip_region = False
                for tr in skip_regions:
                    if center in tr:
                        in_skip_region = True
                        break
                
                if in_skip_region:
                    lower_text = cleaned_text.lower().strip()
                    
                    # キャプション判定（"Table", "Figure"等で始まる説明文）
                    is_caption = lower_text.startswith(("fig.", "fig ", "figure", "table", "tab."))
                    
                    # 本文判定: 長いテキストでアルファベット比率が高い → 表データではなく本文
                    alpha_chars = sum(1 for c in cleaned_text if c.isalpha())
                    total_chars = len(cleaned_text.replace(' ', ''))
                    alpha_ratio = alpha_chars / total_chars if total_chars > 0 else 0
                    is_body_text = len(cleaned_text) > 80 and alpha_ratio > 0.5
                    
                    if is_caption or is_body_text:
                        # キャプション・本文は翻訳対象
                        should_skip = False
                    else:
                        # 表データ（短い、数値メイン）→ スキップ
                        should_skip = True
                
                # 2. 表データ判定（スキップ領域外でも数値メインならスキップ）
                if not should_skip and len(cleaned_text) >= 3:
                    # 数字・記号の比率で表セルを検出
                    alpha_chars = sum(1 for c in cleaned_text if c.isalpha())
                    total_chars = len(cleaned_text.replace(' ', ''))
                    if total_chars > 0:
                        alpha_ratio = alpha_chars / total_chars
                        # アルファベットが20%未満 → 数値/記号メイン → 表データとしてスキップ
                        # ただし短すぎる場合（ページ番号等）は別途判定
                        if alpha_ratio < 0.2 and total_chars > 5:
                            should_skip = True
                
                # 3. セパレータ線自体もスキップ（翻訳しても意味がない）
                if not should_skip and len(cleaned_text) >= 3:
                     # 線記号の数をカウント
                    line_chars = sum(1 for c in cleaned_text if c in "-_=─━")
                    if line_chars / len(cleaned_text) > 0.8:
                        should_skip = True

                # 4. ブロック定義式の判定（レイアウト + テキスト内容の組み合わせ）
                # 論文の method セクション等で独立行として表示される番号付き数式を検出
                # 例: "Σij = 1/(N-1) Σ(x^k − μ)(x^k − μ)^T + εI  (1)"
                if not should_skip and len(cleaned_text) >= 10:
                    page_width = page.rect.width
                    block_center_x = (bbox.x0 + bbox.x1) / 2
                    # ブロックがページ中央付近に配置されている
                    is_centered = abs(block_center_x - page_width / 2) < page_width * 0.15
                    # 左マージンが通常本文より大きい（インデントされている）
                    has_large_left_margin = bbox.x0 > page.rect.x0 + page_width * 0.15
                    
                    if (is_centered or has_large_left_margin) and is_equation_or_code(cleaned_text):
                        should_skip = True

                detailed_blocks.append({
                    "text": cleaned_text,
                    "bbox": bbox,
                    "size": font_size,
                    "font": font_name,
                    "should_skip": should_skip
                })
            except:
                continue

    # 段落結合
    if not detailed_blocks:
        return []
        
    merged_blocks = []
    current_block = detailed_blocks[0]
    
    for next_block in detailed_blocks[1:]:
        # スキップ対象はマージしない
        if current_block["should_skip"] or next_block["should_skip"]:
            merged_blocks.append(current_block)
            current_block = next_block
            continue

        size_diff = abs(current_block["size"] - next_block["size"])
        align_diff = abs(current_block["bbox"].x0 - next_block["bbox"].x0)
        vertical_dist = next_block["bbox"].y0 - current_block["bbox"].y1
        
        if size_diff < 1.0 and align_diff < 5.0 and vertical_dist < current_block["size"] * 1.5:
            current_block["text"] += " " + next_block["text"]
            current_block["bbox"] = current_block["bbox"] | next_block["bbox"]
        else:
            merged_blocks.append(current_block)
            current_block = next_block
            
    merged_blocks.append(current_block)
    
    return merged_blocks


def is_equation_or_code(text: str) -> bool:
    """数式やコード、特殊な記号かどうかを判定
    
    判定対象:
    - 短い数式・変数名（50文字未満で記号比率が高い）
    - 番号付き定義式（末尾に (1), (2) 等がつくブロック数式）
      例: Σij = 1/(N-1) Σ(x^k − μ)(x^k − μ)^T + εI  (1)
    - 長文でも数式記号密度が非常に高いもの
    - コードっぽいテキスト
    """
    import re
    
    text = text.strip()
    if not text:
        return True
    
    # 数式記号セット（基本 + ギリシャ文字 + 数学記号）
    MATH_BASIC = "=+/\\_^[]{}<>|"
    MATH_EXTENDED = MATH_BASIC + "∑∫∏∂∇≤≥≠≈∈∉⊂⊃∀∃±×÷√∞"
    GREEK_LETTERS = "αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    
    # === 1. 番号付き定義式の検出 ===
    # 末尾が (数字) or (数字a) パターン → 論文の定義式番号
    # 例: "M(xij) = √((xij − μij)^T Σ^{-1}_{ij}(xij − μij))   (2)"
    has_equation_number = bool(re.search(r'\(\d+[a-z]?\)\s*$', text))
    
    if has_equation_number:
        # 数式記号 + ギリシャ文字をカウント
        math_symbol_count = sum(1 for c in text if c in MATH_EXTENDED + GREEK_LETTERS)
        # 4文字以上の英単語（意味のある単語）の数
        alpha_words = re.findall(r'[a-zA-Z]{4,}', text)
        
        # 英単語が少なく（≤3語）、数式記号が3つ以上 → 定義式
        if len(alpha_words) <= 3 and math_symbol_count >= 3:
            return True
        # 数式記号の密度が10%以上 → 定義式
        if len(text) > 0 and math_symbol_count / len(text) > 0.1:
            return True

    # === 2. 長文の数式密度チェック ===
    if len(text) >= 50:
        # 長文でも数式記号密度が非常に高い場合はスキップ
        math_symbol_count = sum(1 for c in text if c in MATH_EXTENDED)
        if math_symbol_count / len(text) > 0.15:
            return True
        # 通常の長文は翻訳対象
        return False
    
    # === 3. 既存ロジック: 短文判定 ===
    # 非常に短いテキスト（変数名など）
    # ただし "A." のような箇条書きヘッダーや "Fig 1." は除外したいが
    # ここでは安全側に倒して翻訳しない
    if len(text) < 3 and not any(c.isalpha() for c in text):
        return True
    
    # 数式の特徴（=, +, -, /, \, _, ^, {, } が多い）
    # 短いテキスト（50文字未満）の場合のみチェック
    math_chars = sum(1 for c in text if c in "=+/\\_^[]{}<>|") # - はハイフンとして使われるので除外
    
    # 記号の割合が高い場合
    if len(text) > 0 and (math_chars / len(text)) > 0.3:
        return True
        
    # 特定のキーワード（コードっぽいもの）
    code_keywords = ["import ", "def ", "class ", "return ", "var ", "const ", "function "]
    if any(k in text for k in code_keywords):
        return True
        
    return False

def format_text_for_rect(text: str, rect_width: float, fontsize: float) -> str:
    """Google翻訳テキストをrect幅に合わせて整形する（ルールベース）
    
    機能:
    1. テキスト正規化（不要な改行・余白の除去）
    2. 禁則処理付き折り返し
       - 行頭禁止文字（。、）」等）が行頭に来ないようにする
       - 行末禁止文字（（「等）が行末に来ないようにする
    3. 英単語・数値を途中で分割しない（例: "99.6%" → 一塊で扱う）
    4. 「。」3文ごとに段落インデント（全角スペース字下げ）
    
    Args:
        text: 整形対象の翻訳テキスト
        rect_width: 描画領域の幅（pt）
        fontsize: 使用するフォントサイズ（pt）
    
    Returns:
        整形済みテキスト（\\n区切り）
    
    例:
        入力: "文1。文2。文3。文4。"
        出力: "文1。文2。文3。\\n　文4。"  （3文ごとに段落字下げ）
    """
    import re
    
    # === 1. テキスト正規化 ===
    text = text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    
    if not text or len(text) < 5:
        return text
    
    # === 2. 文字幅推定 ===
    def _is_fullwidth(ch):
        """全角文字判定（CJK、ひらがな、カタカナ、全角記号）"""
        cp = ord(ch)
        return (
            (0x3000 <= cp <= 0x9FFF) or   # CJK統合漢字、ひらがな、カタカナ
            (0xF900 <= cp <= 0xFAFF) or   # CJK互換漢字
            (0xFF01 <= cp <= 0xFF60) or   # 全角英数
            (0xFFE0 <= cp <= 0xFFEF) or   # 全角記号
            (0x20000 <= cp <= 0x2FA1F)    # CJK拡張
        )
    
    def _char_width(ch):
        """1文字の概算幅を返す（pt）"""
        if _is_fullwidth(ch):
            return fontsize       # 全角: 1em
        else:
            return fontsize * 0.55  # 半角: 約0.55em
    
    # === 3. 禁則文字定義 ===
    # 行頭に来てはいけない文字
    NO_LINE_START = set('、。，．・：；？！）］｝〉》」』】〕ー…‥々'
                        'ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ'
                        ',.;:!?)]}')
    # 行末に来てはいけない文字
    NO_LINE_END = set('（［｛〈《「『【〔([{')
    
    # === 4. 行折り返し処理 ===
    # insert_textboxの内部余白を考慮して、少し控えめに設定
    max_line_width = rect_width * 0.95
    
    lines = []
    buf = ""        # 現在の行バッファ
    buf_w = 0.0     # 現在の行幅
    sentence_count = 0  # 文カウント（段落区切り用）
    
    i = 0
    while i < len(text):
        ch = text[i]
        cw = _char_width(ch)
        
        # --- 行に収まる場合: バッファに追加 ---
        if buf_w + cw <= max_line_width:
            buf += ch
            buf_w += cw
            
            # 文末「。」のカウント
            if ch == '。':
                sentence_count += 1
                # 3文ごとに段落区切り: 次の行を全角スペースインデントで開始
                if sentence_count % 3 == 0 and i + 1 < len(text):
                    lines.append(buf)
                    lines.append("")
                    buf = "　"  # 全角スペースインデント
                    buf_w = fontsize
            
            i += 1
            continue
        
        # --- 行が溢れる → 折り返し位置を決定 ---
        break_pos = len(buf)
        
        # (a) 英単語・数値の途中なら単語の先頭まで戻す
        #     例: "AUROC" "99.6%" "ImageNet" を分割しない
        if ch.isascii() and (ch.isalnum() or ch in '.'):
            j = len(buf) - 1
            while j > 0 and buf[j].isascii() and (buf[j].isalnum() or buf[j] in '.-_%'):
                j -= 1
            if j > 0 and j < len(buf) - 1:
                break_pos = j + 1
        
        # (b) 禁則: 次の文字が行頭禁止文字 → 現在行に含める
        if ch in NO_LINE_START:
            buf += ch
            i += 1
            # さらに連続する行頭禁止文字も含める
            while i < len(text) and text[i] in NO_LINE_START:
                buf += text[i]
                i += 1
            lines.append(buf)
            buf = ""
            buf_w = 0.0
            continue
        
        # (c) 禁則: 行末が行末禁止文字 → 次の行に送る
        while break_pos > 1 and buf[break_pos - 1] in NO_LINE_END:
            break_pos -= 1
        
        # 安全策: break_posが0以下にならないように
        if break_pos <= 0:
            break_pos = len(buf)
        
        # 行を確定し、残りを次の行に持ち越す
        lines.append(buf[:break_pos])
        carry = buf[break_pos:]
        buf = carry + ch
        buf_w = sum(_char_width(c) for c in buf)
        i += 1
    
    # 最後の行を追加
    if buf:
        lines.append(buf)
    
    return '\n'.join(lines)


def is_reference_header(text: str) -> bool:
    """参考文献のヘッダーかどうかを判定"""
    keywords = ["references", "bibliography", "参考文献", "cited works"]
    text_lower = text.lower().strip()
    return any(text_lower == k or text_lower == f"{k}:" for k in keywords)

def translate_pdf(pdf_bytes: bytes, source_lang: str = "en", target_lang: str = "ja") -> bytes:
    """PDFを翻訳し、レイアウトを保持した新しいPDFを生成（品質改善版）"""
    import time
    start_time = time.time()
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    # 日本語フォント登録
    font_name = register_font()
    print(f"使用フォント: {font_name}")
    
    # 翻訳統計
    translated_count = 0
    skipped_count = 0
    error_count = 0
    stop_translation = False  # 参考文献以降は翻訳しない
    reference_skipped = False  # 参考文献スキップしたかどうか
    reference_skip_page = 0    # 参考文献が見つかったページ番号
    total_pages = len(doc)
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # フォント埋め込み（ページごと）
        if font_name == "ipaexg":
            try:
                page.insert_font(fontname="ipaexg", fontfile=FONT_PATH)
            except Exception as e:
                print(f"フォント埋め込みエラー: {e}")
                font_name = "china-s"
        
        # 段落結合ブロックの取得
        blocks = extract_and_merge_blocks(page)
        print(f"ページ {page_num + 1}/{len(doc)}: {len(blocks)} 段落")
        
        # 翻訳対象のテキストを抽出（条件付き）
        texts_to_translate = []
        indices_to_translate = []
        
        for i, block in enumerate(blocks):
            text = block["text"]
            
            # 図表・キャプション判定によるスキップ
            if block.get("should_skip"):
                skipped_count += 1
                continue
            
            # 参考文献以降ならスキップ
            if stop_translation:
                continue
                
            # 参考文献ヘッダーの検出
            if is_reference_header(text):
                print(f"  参考文献セクションを検出: {text} (以降の翻訳をスキップ)")
                stop_translation = True
                reference_skipped = True
                reference_skip_page = page_num + 1  # 1-indexed
                continue
            
            # 数式や特殊テキストのスキップ
            if is_equation_or_code(text):
                skipped_count += 1
                continue
                
            # 数字のみ、空文字などはスキップ
            if not text.strip() or text.replace(".", "").isdigit():
                skipped_count += 1
                continue
                
            texts_to_translate.append(text)
            indices_to_translate.append(i)
        
        # バッチ翻訳実行
        if texts_to_translate:
            translated_results = translate_batch(texts_to_translate, source_lang, target_lang)
        else:
            translated_results = []
            
        # 結果をマッピング
        translated_map = {}
        for idx, result in zip(indices_to_translate, translated_results):
            translated_map[idx] = result
        
        # PDFの編集
        for i, block in enumerate(blocks):
            bbox = block["bbox"]
            original_text = block["text"]
            
            # 翻訳結果を取得
            translated_text = translated_map.get(i)

            if translated_map.get(i) is None:
                print("NO_TRANSLATION", i, block["text"][:40], block["bbox"])
            
            # 翻訳結果がある場合のみ処理する（数式や図表はそのまま残す）
            if translated_text:
                # 元のテキスト領域を白で塗りつぶす（元テキスト全体を覆う）
                full_rect = fitz.Rect(bbox)
                page.draw_rect(full_rect, color=None, fill=(1, 1, 1))
                
                # テキスト挿入用rect: 上部に4ptの余白を追加して段落間を視覚的に分離
                rect = fitz.Rect(bbox)
                rect.y0 += 4  # 段落間スペース
                
                
                original_size = block["size"]
                
                # フォントサイズ調整
                font_size = max(original_size * 1.0, 9)
                
                try:
                    # 二分探索で入る最大のフォントサイズを探す
                    # 上限: font_size（元サイズ）、下限: 4pt
                    hi = font_size
                    lo = 4.0
                    best_size = lo
                    
                    # 最大サイズでまず試す
                    formatted = format_text_for_rect(translated_text, rect.width, hi)
                    lh = 1.3
                    rc = page.insert_textbox(
                        rect,
                        formatted,
                        fontsize=hi,
                        fontname=font_name,
                        color=(0, 0, 0),
                        align=fitz.TEXT_ALIGN_LEFT,
                        lineheight=lh
                    )
                    
                    if rc >= 0:
                        best_size = hi
                    else:
                        # 二分探索で最大フォントサイズを探す（精度0.5pt）
                        page.draw_rect(rect, color=None, fill=(1, 1, 1))
                        
                        while hi - lo > 0.5:
                            mid = (hi + lo) / 2
                            lh = min(1.3, max(1.0, mid / font_size * 1.3))
                            
                            # フォントサイズに合わせて再整形
                            formatted = format_text_for_rect(translated_text, rect.width, mid)
                            
                            page.draw_rect(rect, color=None, fill=(1, 1, 1))
                            rc = page.insert_textbox(
                                rect,
                                formatted,
                                fontsize=mid,
                                fontname=font_name,
                                color=(0, 0, 0),
                                align=fitz.TEXT_ALIGN_LEFT,
                                lineheight=lh
                            )
                            
                            if rc >= 0:
                                best_size = mid
                                lo = mid
                            else:
                                hi = mid
                        
                        # 最終描画: best_sizeで確定
                        lh = min(1.3, max(1.0, best_size / font_size * 1.3))
                        formatted = format_text_for_rect(translated_text, rect.width, best_size)
                        page.draw_rect(rect, color=None, fill=(1, 1, 1))
                        page.insert_textbox(
                            rect,
                            formatted,
                            fontsize=best_size,
                            fontname=font_name,
                            color=(0, 0, 0),
                            align=fitz.TEXT_ALIGN_LEFT,
                            lineheight=lh
                        )
                    
                    # [DEBUG]テキスト挿入後に赤枠を描画（白塗りで消されないように最後に描画）
                    # page.draw_rect(rect, color=(1, 0, 0), fill=None, width=0.5)
                    
                    translated_count += 1
                        
                except Exception as e:
                    # フォールバック
                    try:
                        page.insert_text(
                            fitz.Point(bbox[0], bbox[1] + font_size),
                            translated_text,
                            fontsize=font_size,
                            fontname=font_name,
                            color=(0, 0, 0)
                        )
                        error_count += 1
                    except Exception as e2:
                        print(f"テキスト挿入エラー: {e2}")
                        error_count += 1
        
        # リンク注釈（赤や緑の枠線）を削除
        # これらは参考文献へのリンクなどで、一部のPDFビューアで枠が表示されてしまうため
        try:
            for annot in list(page.annots()):
                page.delete_annot(annot)
        except Exception as e:
            print(f"注釈削除エラー: {e}")
            
        # リンク機能（クリック判定）自体も削除
        # 白塗りした後ろにリンク判定が残っていると、クリック時に意図せずジャンプしてしまうため
        try:
            links = list(page.get_links())
            for link in links:
                try:
                    page.delete_link(link)
                except Exception as link_err:
                    print(f"個別リンク削除エラー: {link_err}")
        except Exception as e:
            print(f"リンク取得/削除全体エラー: {e}")
    
    elapsed = time.time() - start_time
    print(f"処理完了: 翻訳 {translated_count}, スキップ {skipped_count}, エラー {error_count} (所要時間: {elapsed:.1f}秒)")
    
    output_buffer = BytesIO()
    doc.save(output_buffer)
    doc.close()
    
    # 追加情報を返す
    extra_info = {
        "elapsed_seconds": round(elapsed, 1),
        "reference_skipped": reference_skipped,
        "reference_skip_page": reference_skip_page,
        "total_pages": total_pages,
        "translated_count": translated_count,
        "skipped_count": skipped_count,
    }
    
    return output_buffer.getvalue(), extra_info


def translate_pdf_streaming(pdf_bytes: bytes, source_lang: str = "en", target_lang: str = "ja"):
    """PDFを翻訳し、ページごとに進捗をyieldするジェネレーター版"""
    import time
    start_time = time.time()
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    # 日本語フォント登録
    font_name = register_font()
    print(f"使用フォント: {font_name}")
    
    # 翻訳統計
    translated_count = 0
    skipped_count = 0
    error_count = 0
    stop_translation = False
    reference_skipped = False
    reference_skip_page = 0
    total_pages = len(doc)
    
    # 初期進捗: 解析開始
    yield {"type": "progress", "page": 0, "total": total_pages, "status": "解析中..."}
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # フォント埋め込み（ページごと）
        if font_name == "ipaexg":
            try:
                page.insert_font(fontname="ipaexg", fontfile=FONT_PATH)
            except Exception as e:
                print(f"フォント埋め込みエラー: {e}")
                font_name = "china-s"
        
        # 段落結合ブロックの取得
        blocks = extract_and_merge_blocks(page)
        print(f"ページ {page_num + 1}/{len(doc)}: {len(blocks)} 段落")
        
        # 翻訳対象のテキストを抽出（条件付き）
        texts_to_translate = []
        indices_to_translate = []
        
        for i, block in enumerate(blocks):
            text = block["text"]
            
            if block.get("should_skip"):
                skipped_count += 1
                continue
            
            if stop_translation:
                continue
                
            if is_reference_header(text):
                print(f"  参考文献セクションを検出: {text} (以降の翻訳をスキップ)")
                stop_translation = True
                reference_skipped = True
                reference_skip_page = page_num + 1
                continue
            
            if is_equation_or_code(text):
                skipped_count += 1
                continue
                
            if not text.strip() or text.replace(".", "").isdigit():
                skipped_count += 1
                continue
                
            texts_to_translate.append(text)
            indices_to_translate.append(i)
        
        # バッチ翻訳実行
        if texts_to_translate:
            translated_results = translate_batch(texts_to_translate, source_lang, target_lang)
        else:
            translated_results = []
            
        # 結果をマッピング
        translated_map = {}
        for idx, result in zip(indices_to_translate, translated_results):
            translated_map[idx] = result
        
        # PDFの編集
        for i, block in enumerate(blocks):
            bbox = block["bbox"]
            original_text = block["text"]
            translated_text = translated_map.get(i)

            if translated_map.get(i) is None:
                print("NO_TRANSLATION", i, block["text"][:40], block["bbox"])
            
            if translated_text:
                full_rect = fitz.Rect(bbox)
                page.draw_rect(full_rect, color=None, fill=(1, 1, 1))
                
                rect = fitz.Rect(bbox)
                rect.y0 += 4
                
                original_size = block["size"]
                font_size = max(original_size * 1.0, 9)
                
                try:
                    hi = font_size
                    lo = 4.0
                    best_size = lo
                    
                    formatted = format_text_for_rect(translated_text, rect.width, hi)
                    lh = 1.3
                    rc = page.insert_textbox(
                        rect, formatted, fontsize=hi, fontname=font_name,
                        color=(0, 0, 0), align=fitz.TEXT_ALIGN_LEFT, lineheight=lh
                    )
                    
                    if rc >= 0:
                        best_size = hi
                    else:
                        page.draw_rect(rect, color=None, fill=(1, 1, 1))
                        
                        while hi - lo > 0.5:
                            mid = (hi + lo) / 2
                            lh = min(1.3, max(1.0, mid / font_size * 1.3))
                            formatted = format_text_for_rect(translated_text, rect.width, mid)
                            page.draw_rect(rect, color=None, fill=(1, 1, 1))
                            rc = page.insert_textbox(
                                rect, formatted, fontsize=mid, fontname=font_name,
                                color=(0, 0, 0), align=fitz.TEXT_ALIGN_LEFT, lineheight=lh
                            )
                            
                            if rc >= 0:
                                best_size = mid
                                lo = mid
                            else:
                                hi = mid
                        
                        lh = min(1.3, max(1.0, best_size / font_size * 1.3))
                        formatted = format_text_for_rect(translated_text, rect.width, best_size)
                        page.draw_rect(rect, color=None, fill=(1, 1, 1))
                        page.insert_textbox(
                            rect, formatted, fontsize=best_size, fontname=font_name,
                            color=(0, 0, 0), align=fitz.TEXT_ALIGN_LEFT, lineheight=lh
                        )
                    
                    # [DEBUG]テキスト挿入後に赤枠を描画（白塗りで消されないように最後に描画）
                    # page.draw_rect(rect, color=(1, 0, 0), fill=None, width=0.5)
                    
                    translated_count += 1
                        
                except Exception as e:
                    try:
                        page.insert_text(
                            fitz.Point(bbox[0], bbox[1] + font_size),
                            translated_text, fontsize=font_size,
                            fontname=font_name, color=(0, 0, 0)
                        )
                        error_count += 1
                    except Exception as e2:
                        print(f"テキスト挿入エラー: {e2}")
                        error_count += 1
        
        # リンク注釈削除
        try:
            for annot in list(page.annots()):
                page.delete_annot(annot)
        except Exception as e:
            print(f"注釈削除エラー: {e}")
            
        try:
            links = list(page.get_links())
            for link in links:
                try:
                    page.delete_link(link)
                except Exception as link_err:
                    print(f"個別リンク削除エラー: {link_err}")
        except Exception as e:
            print(f"リンク取得/削除全体エラー: {e}")
        
        # ページ完了時に進捗をyield
        yield {"type": "progress", "page": page_num + 1, "total": total_pages, "status": "翻訳中..."}
    
    elapsed = time.time() - start_time
    print(f"処理完了: 翻訳 {translated_count}, スキップ {skipped_count}, エラー {error_count} (所要時間: {elapsed:.1f}秒)")
    
    output_buffer = BytesIO()
    doc.save(output_buffer)
    doc.close()
    
    extra_info = {
        "elapsed_seconds": round(elapsed, 1),
        "reference_skipped": reference_skipped,
        "reference_skip_page": reference_skip_page,
        "total_pages": total_pages,
        "translated_count": translated_count,
        "skipped_count": skipped_count,
    }
    
    # 完了時: PDFデータと情報をyield
    pdf_base64 = base64.b64encode(output_buffer.getvalue()).decode('utf-8')
    yield {"type": "complete", "pdf": pdf_base64, "info": extra_info}


def get_pdf_info(pdf_bytes: bytes) -> dict:
    """PDFの情報を取得"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    total_blocks = 0
    for page in doc:
        blocks = extract_and_merge_blocks(page)
        total_blocks += len(blocks)
    
    info = {
        "page_count": len(doc),
        "total_text_blocks": total_blocks
    }
    
    doc.close()
    return info


# ========================================
# API エンドポイント
# ========================================

@app.post("/api/translate-stream")
async def translate_pdf_stream_endpoint(request: TranslateRequest):
    """PDF翻訳 SSEストリーミングエンドポイント（ページ単位の進捗通知）"""
    try:
        pdf_bytes = base64.b64decode(request.pdf)
    except Exception as e:
        # Base64デコードエラーはSSE開始前に検出
        return TranslateResponse(success=False, error=str(e))
    
    def event_generator():
        try:
            for event in translate_pdf_streaming(pdf_bytes, request.source_lang, request.target_lang):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_event = {"type": "error", "error": str(e)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@app.post("/api/translate", response_model=TranslateResponse)
async def translate_pdf_endpoint(request: TranslateRequest):
    """PDF翻訳APIエンドポイント"""
    try:
        # Base64デコード
        pdf_bytes = base64.b64decode(request.pdf)
        
        # PDF情報を取得
        info = get_pdf_info(pdf_bytes)
        
        # PDFを翻訳
        translated_pdf, extra_info = translate_pdf(pdf_bytes, request.source_lang, request.target_lang)
        
        # 追加情報をinfoに統合
        info.update(extra_info)
        
        # Base64エンコード
        pdf_base64 = base64.b64encode(translated_pdf).decode('utf-8')
        
        return TranslateResponse(
            success=True,
            pdf=pdf_base64,
            info=info
        )
        
    except Exception as e:
        print(f"エラー: {e}")
        return TranslateResponse(
            success=False,
            error=str(e)
        )


# 静的ファイル配信
public_dir = os.path.join(os.path.dirname(__file__), "public")
if os.path.exists(public_dir):
    app.mount("/", StaticFiles(directory=public_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
#localhost:8000で起動する




