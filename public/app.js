/**
 * My Readable - PDF翻訳アプリ
 * クライアントサイドロジック（複数ファイル対応版）
 */

// DOM要素
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const uploadSection = document.getElementById('upload-section');
const progressSection = document.getElementById('progress-section');
const completeSection = document.getElementById('complete-section');
const errorSection = document.getElementById('error-section');

const fileNameEl = document.getElementById('file-name');
const progressFill = document.getElementById('progress-fill');
const progressStatus = document.getElementById('progress-status');
const progressPercent = document.getElementById('progress-percent');

const statPages = document.getElementById('stat-pages');
const statBlocks = document.getElementById('stat-blocks');
const statElapsed = document.getElementById('stat-elapsed');
const referenceSkipNotice = document.getElementById('reference-skip-notice');
const referenceSkipMessage = document.getElementById('reference-skip-message');
const progressPageInfo = document.getElementById('progress-page-info');

const downloadBtn = document.getElementById('download-btn');
const downloadAllBtn = document.getElementById('download-all-btn');
const newFileBtn = document.getElementById('new-file-btn');
const retryBtn = document.getElementById('retry-btn');
const errorMessage = document.getElementById('error-message');

// キュー関連DOM
const queueHeader = document.getElementById('queue-header');
const queueCounter = document.getElementById('queue-counter');
const queueList = document.getElementById('queue-list');

// 完了画面
const completeTitle = document.getElementById('complete-title');
const completeSubtitle = document.getElementById('complete-subtitle');
const completeFileList = document.getElementById('complete-file-list');

// ========================================
// 状態管理
// ========================================

/** @type {{ file: File, status: 'waiting'|'processing'|'done'|'error', pdfData: string|null, info: object|null }[]} */
let fileQueue = [];
let currentQueueIndex = 0;
let isProcessing = false;

// ========================================
// イベントリスナー
// ========================================

// ドラッグ＆ドロップ
dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');

    const files = Array.from(e.dataTransfer.files).filter(f => f.type.includes('pdf'));
    if (files.length > 0) {
        handleFiles(files);
    }
});

// ファイル選択
fileInput.addEventListener('change', (e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0) {
        handleFiles(files);
    }
});

// ダウンロードボタン（単一）
downloadBtn.addEventListener('click', () => {
    const doneFiles = fileQueue.filter(q => q.status === 'done');
    if (doneFiles.length === 1) {
        downloadPdf(doneFiles[0].pdfData, doneFiles[0].file.name);
    } else if (doneFiles.length > 0) {
        // 単一ボタンは最後のファイルをDL
        const last = doneFiles[doneFiles.length - 1];
        downloadPdf(last.pdfData, last.file.name);
    }
});

// すべてダウンロードボタン
downloadAllBtn.addEventListener('click', () => {
    const doneFiles = fileQueue.filter(q => q.status === 'done');
    doneFiles.forEach((item, i) => {
        setTimeout(() => {
            downloadPdf(item.pdfData, item.file.name);
        }, i * 500); // 少し間隔を開けてDL
    });
});

// 新しいファイルボタン
newFileBtn.addEventListener('click', resetToUpload);

// リトライボタン
retryBtn.addEventListener('click', resetToUpload);

// ========================================
// 複数ファイル処理
// ========================================

/**
 * 複数ファイルを受け取ってキューに入れ、直列処理を開始
 */
function handleFiles(files) {
    // バリデーション
    const validFiles = [];
    for (const file of files) {
        if (!file.type.includes('pdf')) {
            continue; // PDFでないファイルは無視
        }
        if (file.size > 100 * 1024 * 1024) {
            continue; // 100MB超はスキップ
        }
        validFiles.push(file);
    }

    if (validFiles.length === 0) {
        showError('有効なPDFファイルが見つかりません（対応形式: PDF, 最大100MB）');
        return;
    }

    // キュー初期化
    fileQueue = validFiles.map(file => ({
        file,
        status: 'waiting',
        pdfData: null,
        info: null
    }));
    currentQueueIndex = 0;
    isProcessing = true;

    // 進捗画面を表示
    showSection('progress');

    // キューUIを表示（2ファイル以上のとき）
    const isMultiple = fileQueue.length > 1;
    if (isMultiple) {
        queueHeader.classList.remove('hidden');
        queueList.classList.remove('hidden');
        renderQueueList();
    } else {
        queueHeader.classList.add('hidden');
        queueList.classList.add('hidden');
    }

    // 最初のファイルの名前を表示
    fileNameEl.textContent = fileQueue[0].file.name;

    // 直列処理開始
    processNextInQueue();
}

/**
 * キューリストUIを描画
 */
function renderQueueList() {
    queueList.innerHTML = '';
    fileQueue.forEach((item, index) => {
        const el = document.createElement('div');
        el.className = `queue-item queue-item--${item.status}`;
        el.id = `queue-item-${index}`;

        let statusIcon = '';
        let statusText = '';
        switch (item.status) {
            case 'waiting':
                statusIcon = '⏳';
                statusText = '待機中';
                break;
            case 'processing':
                statusIcon = '🔄';
                statusText = '処理中';
                break;
            case 'done':
                statusIcon = '✅';
                statusText = '完了';
                break;
            case 'error':
                statusIcon = '❌';
                statusText = 'エラー';
                break;
        }

        el.innerHTML = `
            <span class="queue-item-icon">${statusIcon}</span>
            <span class="queue-item-name">${item.file.name}</span>
            <span class="queue-item-status">${statusText}</span>
        `;
        queueList.appendChild(el);
    });
}

/**
 * キュー内の次のファイルを処理
 */
async function processNextInQueue() {
    if (currentQueueIndex >= fileQueue.length) {
        // 全ファイル処理完了
        isProcessing = false;
        showCompleteScreen();
        return;
    }

    const item = fileQueue[currentQueueIndex];
    item.status = 'processing';

    // UI更新
    const isMultiple = fileQueue.length > 1;
    if (isMultiple) {
        queueCounter.textContent = `${currentQueueIndex + 1} / ${fileQueue.length}`;
        renderQueueList();
    }
    fileNameEl.textContent = item.file.name;
    updateProgress(10, 'アップロード中...', 0, 1);

    try {
        const base64Data = await readFileAsBase64(item.file);
        await translatePdfForQueue(base64Data, currentQueueIndex);
    } catch (error) {
        console.error('Queue processing error:', error);
        item.status = 'error';
        if (isMultiple) {
            renderQueueList();
        }
        // 複数ファイルの場合はエラーでも次のファイルに進む
        if (isMultiple) {
            currentQueueIndex++;
            await sleep(500);
            processNextInQueue();
        } else {
            showError(error.message);
        }
    }
}

/**
 * 完了画面を表示
 */
function showCompleteScreen() {
    const doneFiles = fileQueue.filter(q => q.status === 'done');
    const errorFiles = fileQueue.filter(q => q.status === 'error');
    const isMultiple = fileQueue.length > 1;

    if (doneFiles.length === 0) {
        showError('すべてのファイルの翻訳に失敗しました');
        return;
    }

    // 統計の集計
    let totalPages = 0;
    let totalBlocks = 0;
    let totalElapsed = 0;

    doneFiles.forEach(item => {
        if (item.info) {
            totalPages += item.info.total_pages || 0;
            totalBlocks += item.info.translated_count || 0;
            totalElapsed += item.info.elapsed_seconds || 0;
        }
    });

    statPages.textContent = totalPages;
    statBlocks.textContent = totalBlocks;
    statElapsed.textContent = formatElapsedTime(totalElapsed);

    // 参考文献スキップ通知
    if (isMultiple) {
        // 複数ファイル時: 各行にインライン表示するのでブロック通知は非表示
        referenceSkipNotice.classList.add('hidden');
    } else {
        // 単一ファイル時: 従来通りブロック通知
        const refSkipped = doneFiles.find(item => item.info && item.info.reference_skipped);
        if (refSkipped) {
            const skipPage = refSkipped.info.reference_skip_page || 0;
            const tp = refSkipped.info.total_pages || 0;
            referenceSkipMessage.textContent =
                `参考文献以降（${skipPage}ページ目以降 / 全${tp}ページ）の翻訳をスキップしました`;
            referenceSkipNotice.classList.remove('hidden');
        } else {
            referenceSkipNotice.classList.add('hidden');
        }
    }

    // タイトル
    if (isMultiple) {
        completeTitle.textContent = `${doneFiles.length}件の翻訳完了！`;
        completeSubtitle.textContent = errorFiles.length > 0
            ? `${doneFiles.length}件成功 / ${errorFiles.length}件エラー`
            : `${doneFiles.length}件すべて正常に完了しました`;
    } else {
        completeTitle.textContent = '翻訳完了！';
        completeSubtitle.textContent = 'PDFの翻訳が完了しました';
    }

    // 複数ファイルの完了リスト
    if (isMultiple) {
        completeFileList.classList.remove('hidden');
        completeFileList.innerHTML = '';
        fileQueue.forEach((item, index) => {
            const el = document.createElement('div');
            el.className = `complete-file-item complete-file-item--${item.status}`;
            const icon = item.status === 'done' ? '✅' : '❌';
            const statusText = item.status === 'done' ? '完了' : 'エラー';
            const dlBtn = item.status === 'done'
                ? `<button class="complete-file-dl" onclick="downloadPdf(fileQueue[${index}].pdfData, fileQueue[${index}].file.name)">DL</button>`
                : '';
            const elapsed = item.info ? formatElapsedTime(item.info.elapsed_seconds || 0) : '-';
            // 参考文献スキップ情報（インライン表示）
            let skipBadge = '';
            if (item.info && item.info.reference_skipped) {
                const sp = item.info.reference_skip_page || 0;
                skipBadge = `<span class="complete-file-skip">${sp}p〜スキップ</span>`;
            }
            el.innerHTML = `
                <span class="complete-file-icon">${icon}</span>
                <span class="complete-file-name">${item.file.name}</span>
                ${skipBadge}
                <span class="complete-file-elapsed">${elapsed}</span>
                <span class="complete-file-status">${statusText}</span>
                ${dlBtn}
            `;
            completeFileList.appendChild(el);
        });

        // すべてダウンロードボタン表示
        downloadAllBtn.classList.remove('hidden');
        downloadBtn.classList.add('hidden');
    } else {
        completeFileList.classList.add('hidden');
        downloadAllBtn.classList.add('hidden');
        downloadBtn.classList.remove('hidden');
    }

    showSection('complete');
}

// ========================================
// ファイル読み込み・翻訳
// ========================================

/**
 * ファイルをBase64として読み込み
 */
function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();

        reader.onload = () => {
            const base64 = reader.result.split(',')[1];
            resolve(base64);
        };

        reader.onerror = () => {
            reject(new Error('ファイルの読み込みに失敗しました'));
        };

        reader.readAsDataURL(file);
    });
}

/**
 * PDFを翻訳（SSEストリーミング対応・キュー版）
 */
async function translatePdfForQueue(base64Data, queueIndex) {
    const item = fileQueue[queueIndex];

    try {
        // 進捗更新: アップロード完了
        updateProgress(10, 'アップロード完了', 0, 1);
        await sleep(300);

        // SSEストリーミングで翻訳開始
        const response = await fetch('/api/translate-stream', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                pdf: base64Data,
                source_lang: 'en',
                target_lang: 'ja'
            })
        });

        if (!response.ok) {
            throw new Error(`サーバーエラー: ${response.status}`);
        }

        // SSEストリームを読み取り
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // SSEイベントを解析
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed.startsWith('data: ')) continue;

                try {
                    const data = JSON.parse(trimmed.substring(6));

                    if (data.type === 'progress') {
                        const { page, total, status } = data;

                        if (page === 0) {
                            updateProgress(10, '解析中...', 0, 2);
                            progressPageInfo.textContent = `全${total}ページ`;
                        } else {
                            const percent = Math.round(10 + (page / total) * 80);
                            updateProgress(percent, `翻訳中... ${page} / ${total} ページ`, 2, 3);
                            progressPageInfo.textContent = `${page} / ${total} ページ`;
                        }
                    } else if (data.type === 'complete') {
                        updateProgress(100, '完了', 3, 4);
                        await sleep(400);

                        // 結果を保存
                        item.pdfData = data.pdf;
                        item.info = data.info || {};
                        item.status = 'done';

                        const isMultiple = fileQueue.length > 1;
                        if (isMultiple) {
                            renderQueueList();
                        }

                        // 次のファイルへ
                        currentQueueIndex++;
                        await sleep(300);
                        processNextInQueue();
                        return;

                    } else if (data.type === 'error') {
                        throw new Error(data.error || '翻訳中にエラーが発生しました');
                    }
                } catch (parseError) {
                    if (parseError.message && !parseError.message.includes('JSON')) {
                        throw parseError;
                    }
                    console.warn('SSE parse warning:', parseError);
                }
            }
        }

    } catch (error) {
        console.error('Translation error:', error);
        item.status = 'error';

        const isMultiple = fileQueue.length > 1;
        if (isMultiple) {
            renderQueueList();
            currentQueueIndex++;
            await sleep(500);
            processNextInQueue();
        } else {
            showError(error.message);
        }
    }
}

// ========================================
// UI ユーティリティ
// ========================================

/**
 * 進捗を更新
 */
function updateProgress(percent, status, completedStep, activeStep) {
    progressFill.style.width = `${percent}%`;
    progressPercent.textContent = `${percent}%`;
    progressStatus.textContent = status;

    for (let i = 1; i <= 4; i++) {
        const stepEl = document.getElementById(`step-${i}`);
        stepEl.classList.remove('active', 'completed');

        if (i < activeStep) {
            stepEl.classList.add('completed');
        } else if (i === activeStep) {
            stepEl.classList.add('active');
        }
    }
}

/**
 * PDFをダウンロード
 */
function downloadPdf(base64Data, originalFileName) {
    const fileName = originalFileName.replace('.pdf', '_translated.pdf');

    const link = document.createElement('a');
    link.href = `data:application/pdf;base64,${base64Data}`;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

/**
 * セクションを表示
 */
function showSection(sectionName) {
    uploadSection.classList.add('hidden');
    progressSection.classList.add('hidden');
    completeSection.classList.add('hidden');
    errorSection.classList.add('hidden');

    switch (sectionName) {
        case 'upload':
            uploadSection.classList.remove('hidden');
            break;
        case 'progress':
            progressSection.classList.remove('hidden');
            updateProgress(10, 'アップロード中...', 0, 1);
            break;
        case 'complete':
            completeSection.classList.remove('hidden');
            break;
        case 'error':
            errorSection.classList.remove('hidden');
            break;
    }
}

/**
 * エラーを表示
 */
function showError(message) {
    errorMessage.textContent = message;
    showSection('error');
}

/**
 * アップロード画面にリセット
 */
function resetToUpload() {
    fileQueue = [];
    currentQueueIndex = 0;
    isProcessing = false;
    fileInput.value = '';
    progressPageInfo.textContent = '';
    referenceSkipNotice.classList.add('hidden');
    statElapsed.textContent = '-';
    queueHeader.classList.add('hidden');
    queueList.classList.add('hidden');
    queueList.innerHTML = '';
    completeFileList.classList.add('hidden');
    completeFileList.innerHTML = '';
    downloadAllBtn.classList.add('hidden');
    downloadBtn.classList.remove('hidden');
    showSection('upload');
}

/**
 * 所要時間を「○分○秒」形式にフォーマット
 */
function formatElapsedTime(seconds) {
    const totalSec = Math.round(seconds);
    if (totalSec < 60) {
        return `${totalSec}秒`;
    }
    const hours = Math.floor(totalSec / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    const secs = totalSec % 60;
    if (hours > 0) {
        return `${hours}時間${String(mins).padStart(2, '0')}分${String(secs).padStart(2, '0')}秒`;
    }
    return `${mins}分${String(secs).padStart(2, '0')}秒`;
}

/**
 * スリープ
 */
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
