# Contributing

Thank you for improving this reference implementation.

This repository is a completed implementation showcase. It does not accept new
features or provider expansions. Contributions are limited to security
maintenance and documentation corrections that preserve published behavior.

## Before opening a change

1. Read the README for the component you plan to change.
2. Keep deployment-private material, infrastructure, CI/CD, credentials, and
   customer content out of the repository.
3. Use synthetic data and placeholder configuration only.
4. Do not change a public interface or support commitment.

## Local verification

Follow the component README and run the smallest existing test, build, or static
check that covers the edited code.

For content-source adapter or synthetic gallery changes, run:

```bash
python3 scripts/verify_offline_content_flow.py
```

For Reading Room frontend changes:

```bash
cd Layer3_Campfire/frontend
cp .env.example .env.local
bun install --frozen-lockfile
bun run check
bun run test -- --runInBand
bun run build
```

## Pull requests

- Explain the user or maintainer problem and the chosen boundary.
- Link the issue the change addresses.
- List the exact local checks run.
- Call out external services or data that prevented verification.
- Update documentation and example configuration when behavior changes.
- Do not include generated dependency directories or local environment files.

Small, focused changes are easier to review. New dependencies need a clear
purpose, license review, and appropriate component-governance review.
Reviews and contribution intake are best effort. This reference implementation
does not provide a support, response-time, compatibility, or operational SLA.

## Content and data

Do not submit personal data, visitor data, staff data, customer exports,
copyrighted archival material without documented rights, or production-derived
prompts and outputs. Use clearly synthetic fixtures that cannot be mistaken for
real records.

## Reporting security issues

Do not open public issues for suspected vulnerabilities. Follow
[SECURITY.md](SECURITY.md).
