# AI Lecture Service

A FastAPI microservice to handle AI‐driven lecture pipeline jobs (ingestion, image analysis, embedding, explanation, summarization).

## Setup

1. Ensure Python 3.13+ (<4.0) is installed.
2. Clone the repository.
3. Create a `.env` file in the project root with the environment variables listed below.
4. Install dependencies:
   ```
   poetry install
   ```
5. Run the server:
   ```
   poetry run start
   ```

## Environment Variables

Set the following environment variables in .env.example.

## Testing

Run tests with:

```
poetry run pytest
```

## CI/CD Workflow

### Staging Environment

1. A developer writes code on a feature branch and opens a Pull Request to `main`.
2. After code review and approval, the PR is merged.
3. The merge to `main` automatically triggers a GitHub Actions workflow (`cd.yml`).
4. This workflow builds a Docker image tagged with the commit SHA and deploys it to the **staging** environment.

### Production Environment

1. After changes are verified in staging, a release can be deployed to production.
2. A developer creates and pushes a semantic version git tag (e.g., `v1.2.3`) from the `main` branch.
   ```bash
   # From the main branch
   git tag -a v1.0.0 -m "Release notes"
   git push origin v1.0.0
   ```
3. Pushing the tag automatically triggers the release workflow (`release.yml`).
4. This workflow builds a Docker image tagged with the version (e.g., `v1.0.0`) and deploys it to the **production** environment.
