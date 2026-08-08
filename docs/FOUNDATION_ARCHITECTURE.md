# Python基盤とImageOCR2Excel固有層

この文書は開発者向けです。導入方法は `README.md`、起動後の操作は
`docs/USER_GUIDE.md` を参照してください。

## 境界

### 共通基盤

- `ImageOCR2Excel/models.py`
  - `TemplateField`、任意の `SetDefinition`、座標・文字整形設定
  - セット出力では `set_id` / `slot_key` だけを標準語彙として扱う
- `ImageOCR2Excel/application.py`
  - OCR範囲編集、認識テスト、Excel・CSV出力の共通UI
  - 起動時に `OcrProfile` と `ApplicationConfig` を必須で受け取る
- `ImageOCR2Excel/export/`
  - `SetDefinition` に従った表形式出力
- `ImageOCR2Excel/ui/theme.py`
  - 色、寸法、フォント、アプリを問わない操作文だけを保持する

### ImageOCR2Excelアプリ層

`ImageOCR2Excel/apps/image_ocr.py` が次を組み合わせる。

- `get_profile("generic")`
- ウィンドウ名、AppDataフォルダー名、既定テンプレート名
- 開始方法などのアプリ固有コピー
- アプリ本体とランチャーの起動

### OCRプロファイル

`profiles/generic.py` は汎用画像OCRの既定値だけを持つ。

- `profile_id = "generic"`
- 既定バックエンドは `paddle`
- 座標系は画像全体基準
- 自動検出ストラテジーは持たない
- UIにはプロファイル選択を出さない

## テンプレート互換性

- 新しいImageOCR2Excelテンプレートは version 1 から開始する。
- MVP期の旧version 3テンプレートは移行しない。
- 保存形式には `profile_id`、`ocr_backend`、`coordinate_settings`、
  `text_formatting`、`set_definition` を含める。
- Tesseract設定は保存形式へ含めない。

## OCRバックエンド

現在の実装済みバックエンドは `paddle` のみ。モデルには
`ocr_backend` を残し、将来 `easyocr`、`rapidocr`、`cloud`、
`custom_cli` などを追加できる境界を維持する。
