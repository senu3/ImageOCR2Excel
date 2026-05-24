# Image OCR to Excel

画像プレビュー上で複数の読み取り範囲を指定し、各範囲のOCR結果をExcelセルへ書き込むWindows向けアプリです。

## 機能

- PNG/JPEGなどの画像を読み込み、プレビュー上で矩形範囲をドラッグ選択
- 複数範囲を登録し、範囲ごとにExcelセルを指定
- OCR結果を確認・手修正してからExcelへ反映
- 範囲設定をJSONで保存・読み込み
- 既存の`.xlsx`を更新、または新規`.xlsx`として作成

## セットアップ

```powershell
uv sync
```

OCRにはTesseract OCR本体が必要です。Windowsでは以下をインストールし、日本語データを含めてください。

- Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
- インストール時にJapanese language dataを選択

Tesseractのパスが自動検出されない場合は、アプリ右上の「Tesseract」欄に例のように入力してください。

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

## 起動

```powershell
uv run image_ocr_excel_app.py
```

PowerShellランチャーを使う場合は以下です。

```powershell
.\run_app.ps1
```

## 使い方

1. 「画像を開く」でスクリーンショットを選択
2. 「Excelを選択」で出力先`.xlsx`を選択、または新規ファイル名を指定
3. 必要に応じてシート名を変更
4. 画像上をドラッグして範囲を作成
5. 右側の一覧で各範囲のセルを入力
6. 「選択範囲をOCR」で読み取り結果を確認
7. 「Excelへ反映」で指定セルへ書き込み

添付画像のような例では、ポケモン名やサブスキル欄をそれぞれ範囲選択し、`A2`, `B2`, `C2`, `D2`のように対応セルを指定します。
