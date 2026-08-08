# テスト

通常のテストはOCRモデルや検証画像を必要としません。

```powershell
uv run python -m unittest discover -s tests
```

現在のテストは、汎用プロファイル、version 1テンプレート、旧形式の拒否、Excelの画像単位出力を確認します。
