# Axiom Angular frontend

See the [root README](../README.md) for the feature inventory, Python setup, broker configuration and deployment.

Use Node 22.22.3+ (22.x) or 24.15+:

```bash
npm ci
npm start       # Angular :4200; API proxy targets Flask :8080
npm run build   # Flask serves dist/frontend/browser
npm test        # Production build plus isolated Python/Playwright browser checks
```

`npm test` requires the root `.venv`, `requirements-dev.txt` and Playwright Chromium. Standalone pages live in `src/app/pages`; shared API/session state is in `src/app/core`; reusable visual components are in `src/app/shared`.
