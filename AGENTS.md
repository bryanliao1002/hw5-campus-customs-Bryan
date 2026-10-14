# HW5 Project Instructions

These instructions apply only to HW5 and override conflicting AI model instructions inherited from parent folders.

## AI calls

- Route all AI requests through Portkey, authenticated with `PORTKEY_API_KEY` loaded from the root `.env` file or environment.
- Use `gpt-6-luna` exclusively. Do not configure or call another model, including as a fallback.
