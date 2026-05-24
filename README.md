# Image OCR to Excel

画像プレビュー上で複数の読み取り範囲を指定し、各範囲のOCR結果をExcelセルへ書き込むWindows向けアプリです。

## 機能

- PNG/JPEGなどの画像を読み込み、プレビュー上で矩形範囲をドラッグ選択
- 複数範囲を登録し、範囲ごとにExcelセルを指定
- OCR結果を確認・手修正してからExcelへ反映
- 範囲設定をJSONで保存・読み込み
- 現在開いているExcelブック・シートへ直接反映
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
2. Excelで反映先ブックを開く
3. 「開いているExcel更新」でブックとシートを取得
4. 「開いているブック」と「シート」を選び、「開いているExcelを使用」を押す
5. 画像上をドラッグして範囲を作成
6. 右側の一覧で各範囲のセルを入力
7. 「選択範囲をOCR」または「全範囲をOCR」で読み取り結果を確認
8. 「Excelへ反映」で指定セルへ書き込み

ファイルへ直接保存したい場合は、「Excelを選択」で出力先`.xlsx`を選択し、「ファイル出力を使用」を押してください。

## Excel連携

開いているExcelの自動取得にはWindowsのExcel COM連携を使用します。`uv sync`で`pywin32`もインストールされます。

開いているExcelへ反映した場合、セルには値を書き込みますが自動保存はしません。保存はExcel側で行ってください。

添付画像のような例では、ポケモン名やサブスキル欄をそれぞれ範囲選択し、`A2`, `B2`, `C2`, `D2`のように対応セルを指定します。
