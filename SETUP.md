# Cloud setup

Use a Cloud checkout of the approved repository. Select Python 3.11 or later and configure the custom setup script as `./setup.sh` from the repository root. The script creates a fresh virtual environment when needed, installs pinned dependencies and runs the offline suite. Use the same command for cached environment maintenance when dependencies change. Optional PDF export requires a compatible LibreOffice binary named libreoffice or soffice.

For a direct script smoke test in Cloud, run `.venv/bin/python .agents/skills/seo-growth-strategy/scripts/preflight.py --help`. The normal entry point is the Codex skill picker or supported skill mention.

Provision connectivity for the repository and package registry during setup, public target and competitor domains during research, and OpenSEO via its approved connector. In an allowlisted environment include approved apex and www destinations and review additional redirects before adding hosts. Keep service credentials in the authorized integration. Use no credentials in crawl requests.
