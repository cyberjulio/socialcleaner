# Contributing to SocialCleaner

Thanks for your interest in contributing.

## Getting Started

```bash
git clone https://github.com/cyberjulio/socialcleaner.git
cd socialcleaner
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
cp .env.example .env  # add your CLEANER_SECRET_KEY
```

## How to Contribute

- **Bug reports** — open an issue with steps to reproduce and what you expected vs. what happened
- **Feature requests** — open an issue before writing code so we can discuss the approach
- **Pull requests** — keep changes focused; one feature or fix per PR

## Pull Request Guidelines

1. Fork the repo and create a branch from `main`
2. Test your changes locally before submitting
3. Keep the PR description clear about what changed and why
4. For frontend changes, rebuild the bundle: `cd frontend && npm run build`

## Security Issues

Do not open public issues for security vulnerabilities — see [SECURITY.md](SECURITY.md).
