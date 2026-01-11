# MiniClue AI Service (`miniclue-ai`)

The Python microservice responsible for the heavy lifting in the MiniClue platform. It handles PDF ingestion, RAG pipeline processing, and LLM inference.

**Role in Stack:**

- **Ingestion:** Parses PDFs and extracts text/images (PyMuPDF).
- **Worker:** Consumes Google Pub/Sub messages for async processing.
- **AI:** Generates embeddings (OpenAI) and handles Chat streaming.

## 🛠 Prerequisites

- **Python 3.13+**
- **Poetry** (Dependency Management)
- **Supabase** (Postgres & Storage access)
- **Google Cloud SDK** (For local authentication)

## 🚀 Quick Start

> See [CONTRIBUTING.md](https://github.com/miniclue/miniclue-info/blob/main/CONTRIBUTING.md) for full details on how to setup and contribute to the project.

1. **Fork & Clone**

```bash
# Fork the repository on GitHub first, then:
git clone https://github.com/your-username/miniclue-ai.git
cd miniclue-ai
git remote add upstream https://github.com/miniclue/miniclue-ai.git
poetry install
```

2. **Environment Setup**
   Copy the example config:

```bash
cp .env.example .env

```

_Ensure you populate all fields as stated in the `.env.example` file._

3. **Run Locally**

```bash
poetry run start
# Service will run at http://127.0.0.1:8000

```

4. **Always format and lint your code before committing**

```bash
poetry run black .
poetry run ruff check .
```

## 📝 Pull Request Process

1. Create a new branch for your feature or bugfix: `git checkout -b feature/my-cool-improvement`.
2. Ensure your code follows the coding standards and project architecture.
3. Push to your fork: `git push origin feature/my-cool-improvement`.
4. Submit a Pull Request from your fork to the original repository's `main` branch.
5. Provide a clear description of the changes in your PR.
6. Once your PR is approved and merged into `main`, the CI/CD pipeline will automatically deploy it to the [staging environment](https://stg.svc.miniclue.com) for verification.
7. Once a new release is created, the CI/CD pipeline will automatically deploy it to the [production environment](https://svc.miniclue.com).

> Note: Merging of PR and creation of release will be done by repo maintainers.
