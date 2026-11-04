# Synthetic Data Generator → ADLS Gen2

```
config.yaml          ADLS account/container, env var for the key, batches, output format
datasets/*.json      one file per dataset (columns + rows per batch)
datagen.cmd          shortcut:  datagen run | datagen clean | datagen plan
datagen/             source code
```

## Setup (once)

```powershell
pip install -r requirements.txt
setx ADLS_ACCESS_KEY "<storage account access key>"   # name must match adls.access_key_env
```
Then edit `config.yaml` (`account_name`, `container`, `root_path`, `batches`).

## Commands

| Command | What it does |
|---|---|
| `datagen plan` | Checks the config and datasets, shows the batches and example paths, and confirms the key env var is set |
| `datagen run` | **Deletes everything under `adls.root_path`**, then generates and uploads every batch |
| `datagen clean` | Only deletes |
| `datagen run --local .\out` | Same as `run`, but writes to a local folder (for testing) |
| `datagen run --no-clean` | Appends without deleting |
| `-c other.yaml` | Uses a different config file |

(`python -m datagen run` is the same as `datagen run`.)

## Batches (`config.yaml`)

| Keys given | Result |
|---|---|
| `start_date` + `end_date` + `interval` | One batch per interval (`end_date` is inclusive when you give only a date) |
| `start_date` + `interval` + `count` | `count` batches in a row |
| `start_date` + `end_date` + `count` | The range split into `count` equal windows |
| `count` only | `count` daily batches starting today |

Interval units: `s`, `min`/`m`, `h`, `d`, `w`, `mo`, `y`. Each `date`/`timestamp` column gets values inside its batch's window.
The file layout comes from `output.path_template`, which can use `{dataset} {batch_id} {batch_start} {batch_end} {ext}`.

## Dataset JSON

```json
{
  "name": "orders",                      // optional, defaults to the file name
  "rows_per_batch": 100,                 // or {"min": 100, "max": 200}
  "format": "parquet",                   // optional, overrides output.format
  "columns": [ { "name": "...", "type": "...", ... } ]
}
```

Any column can also take `"null_ratio": 0.1`.

| type | options |
|---|---|
| `sequence` | `start` (1), `step` (1). Keeps counting across batches, so values stay unique |
| `uuid` | none |
| `int` | `min`, `max` |
| `float` | `min`, `max`, `precision`, `distribution`: `uniform` or `normal` (`mean`, `std`) |
| `string` | `length`, or `min_length` + `max_length`; `chars`, `prefix` |
| `choice` | `values`, `weights` |
| `bool` | `true_ratio` |
| `date` / `timestamp` | Batch window by default, or a fixed `min`/`max`; `format` (strftime), `sorted` |
| `faker` | `provider` (any [Faker](https://faker.readthedocs.io/en/master/providers.html) method), `args`, `unique` |
| `template` | `pattern` such as `"CUST-{customer_id:07d}"`. Can use earlier columns plus `batch_id`, `row_number` |
| `reference` | `dataset`, `column`, `scope`: `batch` (default) or `all`. Foreign key to another dataset; parent datasets are generated first |
| `batch` | `field`: `id`, `start` or `end` |
| `constant` | `value` |

See `datasets/customers.json` and `datasets/orders.json` for working examples.
