# Ministry of Justice Integration Hub

[![Ministry of Justice Repository Compliance Badge](https://github-community.service.justice.gov.uk/repository-standards/api/integration-hub/badge)](https://github-community.service.justice.gov.uk/repository-standards/integration-hub)

This repository holds files relevant to the Ministry of Justice Integration Hub.

It contains the [Integration Hub user guide](https://user-guide.integration-hub.service.justice.gov.uk), architectural decision records, and other design artefacts.

It will also serve as a landing page for the team's backlog via GitHub Projects.

## Local Development

The site uses the GOV.UK [tech-docs-gem](https://github.com/alphagov/tech-docs-gem).
Docker provides the dependencies; no local Ruby or Node.js installation is required.

### Requirements

- **macOS:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) and Make
  (available through Xcode Command Line Tools: `xcode-select --install`).
- **Linux:** [Docker Engine](https://docs.docker.com/engine/install/) and Make, installed
  through your distribution's package manager. Ensure your user can run Docker commands.
- **Windows:** [WSL 2](https://learn.microsoft.com/en-us/windows/wsl/install) and
  [Docker Desktop with WSL integration](https://docs.docker.com/desktop/features/wsl/).
  Install Make in your WSL distribution and run the commands below in its Linux terminal,
  not PowerShell or Command Prompt.

Start Docker before continuing. On ARM machines, including Apple Silicon Macs, the preview
uses an AMD64 image and requires emulation support. Docker Desktop provides this by default;
ARM Linux users must [configure emulation](https://docs.docker.com/build/building/multi-platform/#qemu).

### Preview the Site

Clone this repository and, from its root directory, run:

```sh
make preview
```

Then open <http://127.0.0.1:4567>.

The first run downloads the Docker image and may take a few minutes. Edit the documentation
in `source/` and refresh your browser to see changes. Press **Ctrl+C** to stop the preview;
your local files are retained.

If port 4567 is already in use, run `make preview PORT=4568` and open <http://127.0.0.1:4568>.

The [Makefile](Makefile) contains the Docker command and uses the same pinned publisher image
as the [publishing workflow](.github/workflows/documentation.yml).