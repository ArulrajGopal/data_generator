# How to Run

Run from the project folder.

## PowerShell

| Command | What it does |
|---|---|
| `.\datagen.cmd plan` | Checks config and datasets, shows the batch plan. Changes nothing |
| `.\datagen.cmd run --local .\out` | Generates files into a local folder (test run) |
| `.\datagen.cmd run` | **Deletes everything under `root_path`**, then generates and uploads to ADLS |
| `.\datagen.cmd run --no-clean` | Uploads without deleting existing files |
| `.\datagen.cmd clean` | Only deletes everything under `root_path` |
| `.\datagen.cmd run -c .\other.yaml` | Uses a different config file (works with any command) |
| `.\datagen.cmd --help` | Shows help |

## Python

Works in any terminal (PowerShell, cmd.exe, bash).

| Command | What it does |
|---|---|
| `python -m datagen plan` | Checks config and datasets, shows the batch plan. Changes nothing |
| `python -m datagen run --local .\out` | Generates files into a local folder (test run) |
| `python -m datagen run` | **Deletes everything under `root_path`**, then generates and uploads to ADLS |
| `python -m datagen run --no-clean` | Uploads without deleting existing files |
| `python -m datagen clean` | Only deletes everything under `root_path` |
| `python -m datagen run -c .\other.yaml` | Uses a different config file (works with any command) |
| `python -m datagen --help` | Shows help |

## Command Prompt (cmd.exe)

Same commands without `.\`, e.g. `datagen plan`, `datagen run`.
