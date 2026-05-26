# 🤝 Contributing

## ⚙️ Development setup

```bash
git clone https://github.com/jctots/pdf-translate.git
cd pdf-translate
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements-dev.txt
python app.py
```

Open <http://localhost:7860>.

## 🧪 Running tests

```bash
pytest
```

No live services required — all HTTP calls are mocked. See [docs/testing.md](docs/testing.md) for details.

## ➕ Adding a translation backend

1. Create `backends/<name>.py` with:
   - `call(text, source, target, **params) -> str` — translates one text block
   - `test_connection(...) -> str` (optional — used by the UI connection test button)
2. Add `elif service == "<Name>"` branches in `backends/__init__.py` for both `translate()` and `translate_sync()`.
3. Add unit tests in `tests/test_backends.py` (mock all HTTP calls).
4. Add any new UI inputs to `app.py` and thread them through `translate()`.

No `translate_pdf()` wrapper is needed — the pipeline is handled centrally in `pipeline.py`.

## 📬 Submitting changes

- Open an issue before starting significant work.
- Keep pull requests focused — one feature or fix per PR.
- All tests must pass: `pytest`.
- Follow the existing code style (no formatter enforced; match surrounding code).

## 📋 Changelog

Release history is tracked in [GitHub Releases](https://github.com/jctots/pdf-translate/releases).

## 🐛 Reporting bugs

Open a [GitHub issue](../../issues) with:
- What you did
- What you expected
- What happened (include the error message or traceback)
- Python version, OS, and which backend you were using
