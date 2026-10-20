# Demo Workspace 🧪

The `demo_workspace/` directory provides sample files and test suites designed for exploring Metis in a safe sandbox environment without touching production projects.

---

## 📁 File Overview

| File | Type | Purpose |
| :--- | :--- | :--- |
| [`auth.py`](auth.py) | Python Source | Authentication helper implementing user verification, token generation, and token expiration logic. Ideal for testing surgical edits and refactoring. |
| [`test_auth.py`](test_auth.py) | Python Unit Test | Unit test suite for `auth.py`. Can be run by Metis via the silent terminal runner (`python -m unittest demo_workspace/test_auth.py`) to verify edits in real time. |
| [`quicksort.py`](quicksort.py) | Python Source | Implementation of the quicksort algorithm with pivot selection. Useful for algorithm optimizations and explanation tasks. |
| [`spec_sample.pdf`](spec_sample.pdf) | PDF Document | Sample architecture specification PDF used to test PDF attachment, stream text extraction (`extract_pdf_text`), and multimodal LLM document reading. |
| [`diagram_sample.png`](diagram_sample.png) | Image Asset | Sample system diagram image used to verify multimodal image ingestion, visual inspection, and diagram reasoning. |

---

## 💡 Quick Experiment Suggestions

1. **Surgical Refactoring**:
   * Add `demo_workspace/auth.py` and `demo_workspace/test_auth.py` to the whitelist.
   * Prompt Metis: *"Add exponential backoff retry to token validation in auth.py, update the test suite in test_auth.py, and run the tests."*
2. **Multimodal Analysis**:
   * Add `demo_workspace/spec_sample.pdf` or `demo_workspace/diagram_sample.png` to the whitelist.
   * Prompt Metis: *"Summarize the architecture specifications from the attached PDF document."*
