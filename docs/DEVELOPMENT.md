# 開発ガイド

## 環境構築と起動

Python環境と依存関係は `uv` で管理します。対応Pythonは3.10以降で、`.python-version` では3.12を指定しています。

```powershell
uv sync
uv run python main.py
```

Windows用ランチャーの動作を確認する場合は、`ImageOCR2Excel.cmd` または `run_app.ps1` を使用します。

```powershell
.\ImageOCR2Excel.cmd
.\run_app.ps1 --repair
```

`--repair` は `uv.lock` に記録された依存関係を再インストールします。OCR認識モデル、テンプレート、端末設定は削除しません。

## 汎用版の構成

- `main.py`: アプリ本体の起動エントリーポイント
- `launcher.py`: 専用ランチャーの起動エントリーポイント
- `ImageOCR2Excel/apps/image_ocr.py`: 汎用プロファイルとアプリ設定の組み立て
- `ImageOCR2Excel/application.py`: CustomTkinter UIと画面遷移
- `ImageOCR2Excel/profiles/generic.py`: 汎用画像OCRの既定値
- `ImageOCR2Excel/ocr/`: OCR実行、画像処理、認識モデルの準備状態
- `ImageOCR2Excel/export/`: Excel・CSV出力と画像キュー
- `ImageOCR2Excel/models.py`: テンプレート項目、セット、座標、文字整形のモデル
- `ImageOCR2Excel/templates.py`: テンプレートの保存・読み込み
- `ImageOCR2Excel/persistence.py`: アトミック保存
- `ImageOCR2Excel/diagnostics.py`: 診断ログと未処理例外の記録
- `ImageOCR2Excel/launcher/`: 実行環境の準備、修復、起動UI

基盤とアプリ設定の境界は[基盤アーキテクチャ](FOUNDATION_ARCHITECTURE.md)を参照してください。

## MegidoOCR2Excelからの汎用化

汎用版はMegidoOCR2Excelの共通基盤を利用し、次のゲーム固有機能を含みません。

- 会話画面・会話ログの自動検出
- 話者とセリフ用の項目プリセット
- 黒余白を基準にしたゲーム画面座標補正
- 横線記号を画像形状から判定する補正

`ImageOCR2Excel/apps/image_ocr.py` は `generic` プロファイルを固定で選択します。汎用版では画像全体を座標基準とし、利用者が読み取り範囲を指定します。

## OCR準備状態

OCR環境マネージャーは、依存パッケージ、モデル保存先、検証済みモデルファイル、PaddleOCR・PaddlePaddleのバージョンを確認します。

**「ダウンロードして準備」** または **「確認して更新」** は専用の子プロセスでOCRを初期化し、不足モデルを取得します。保存先やパッケージ構成が変わると、次回起動時に明示確認を求めます。

認識モデルは既定で `%USERPROFILE%\.paddlex` に保存されます。保存先は端末設定であり、テンプレートには含まれません。

## テンプレート形式

正式形式は `format: image-ocr-to-excel-template`、`version: 1`、`profile_id: generic` です。読み込み時に形式、バージョン、必須項目、プロファイル、OCRバックエンドを検証します。

汎用版はPaddleOCRだけをサポートします。旧MVPのversion 3テンプレート、MegidoOCR2Excelのプロファイル、Tesseract設定の互換読み込みは行いません。形式を変更する場合はバージョンを更新し、必要に応じて移行処理を設計してください。

## 出力と保存の保護

- 空の認識結果や処理失敗はExcelの `Errors` シートへ記録する
- セット出力の通知は必要な場合だけ `Notices` シートへ記録する
- 失敗分の再実行は対象画像の前回行だけを置き換える
- テンプレートとExcelは一時ファイルへ書き切った後に置き換える
- 読み取り範囲は作成元画像のサイズとともに保存し、解像度差に合わせてスケーリングする

診断ログは一定サイズでローテーションし、OCRの認識結果は記録しません。

## テスト

通常のテストは、OCRモデルや非公開画像がない環境でも実行できます。

```powershell
uv run python -m unittest discover -s tests
```

構文確認を含めて実行する場合:

```powershell
uv run python -m compileall -q main.py launcher.py ImageOCR2Excel tests
uv run python -m unittest discover -s tests
uv run python launcher.py --check
```

`launcher.py --check` は未準備環境では終了コード2を返します。JSON出力の `ready`、`reason`、`uv_available` を確認してください。
