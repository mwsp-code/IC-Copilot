# Launch Assets

- `ic-story-aapl.png`: local no-network AAPL demo screenshot used by the repository README.
- `social-preview.png`: 1280 x 640 GitHub social preview candidate.
- `scripts/generate_social_preview.ps1`: deterministic source for the social preview.

Regenerate the social preview on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/generate_social_preview.ps1
```

Refresh the app screenshot only from the sanitized demo. Do not capture API-key fields, local paths,
provider credentials, licensed report text, or personal browser chrome.
