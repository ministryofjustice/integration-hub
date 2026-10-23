# Contributing

We use the Integration Hub AI toolkit to keep AI-assisted contributions
consistent with our engineering standards. Follow the steps below to set it up.


### 1. Install APM

Follow the [APM installation guide](https://microsoft.github.io/apm/getting-started/installation/),
then verify:

```sh
apm --version
```

### 2. Install the toolkit

From a terminal at the repository root, install the dependencies declared in
`apm.yml`:

```sh
apm install --target copilot
```

For other AI agents, follow
[APM's target selection examples](https://microsoft.github.io/apm/reference/cli/targets/#examples)
to select the appropriate `--target`.

If your agent is not following the toolkit guidance, check that you
installed it for the correct target and that the agent has loaded the
instructions. 

## Update the toolkit

To update the shared toolkit version, run:

```sh
apm update
```

Review the changes and open a PR with the updated
`apm.lock.yaml` and any `apm.yml` changes. Do not commit generated instructions
or edit them locally. You can propose instruction changes in the [MoJ AI toolkit](https://github.com/ministryofjustice/ai-toolkit) repository. Update the
[`integration-hub` folder](https://github.com/ministryofjustice/ai-toolkit/tree/main/toolkits/integration-hub/engineering),
which contains our team instructions. 

## Before opening a pull request

- Follow the toolkit guidance and review all AI-generated changes.
- Run the relevant [documentation checks](README.md#local-development) or
  [file-transfer tests](tests/file-transfer/README.md). Follow the test guide's
  access requirements before running live AWS tests.
- Explain what changed, why, and how it was checked, including any checks not run.
- Never include secrets or sensitive data in prompts, logs, or pull requests.
