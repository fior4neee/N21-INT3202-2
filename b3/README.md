# MongoDB 8.0 sharded-cluster lab

Simulates MongoDB sharded-cluster behaviour on one Windows machine with Docker Desktop.
Everything is orchestrated from **PowerShell with native Windows Python** (pymongo + the
`docker` CLI). No WSL distro, no bash and no host `mongosh` are needed, because the
`mongo:8.0` image includes mongosh and the scripts call it with `docker exec`.

## Topology

| Component | Containers | Replica set | Host ports |
|---|---|---|---|
| Config servers | cfg1, cfg2, cfg3 | cfgRS | 27101-27103 |
| Shard 1 | s1a, s1b, s1c | shard1RS | 27104-27106 |
| Shard 2 | s2a, s2b, s2c | shard2RS | 27107-27109 |
| Shard 3 (E9 only) | s3a, s3b, s3c | shard3RS | 27110-27112 |
| Router | mongos | - | **27100** (see note) |

* Every mongod: `--wiredTigerCacheSizeGB 0.25`, `mem_limit 640m`, fixed hostname, healthcheck,
  named Docker volume (no Windows bind mounts). Inside the Docker network every node is `<name>:27017`.
* Chunk size = 8 MB (`config.settings`). No keyfile/auth (local lab simplification).
* **Note on the mongos port:** this machine runs a native *MongoDB* Windows service bound to
  `127.0.0.1:27017`, which takes priority over Docker's `0.0.0.0:27017`. mongos is therefore
  published on **27100**. If you stop that service (`Stop-Service MongoDB`, admin), you can add
  `MONGOS_PORT=27017` to `.env`. Compose and the Python scripts both read it.
* Connect with a GUI (Compass): `mongodb://127.0.0.1:27100/`. A single node: `mongodb://127.0.0.1:27104/?directConnection=true`.

## Prerequisites (once)

```powershell
cd C:\GitHub\N21-INT3202-2\b3
python -m pip install -r requirements.txt
# Docker Desktop must be running:
docker info --format '{{.ServerVersion}}'
```

Create `b3\.env` (git-ignored, never printed by the scripts):

```
ATLAS_URI=mongodb+srv://<user>:<password>@<cluster>/
```

## Cluster commands

```powershell
python -m lab.cluster up       # start containers, initiate replica sets, addShard, chunk size
python -m lab.cluster status   # node/role table, shards, balancer
python -m lab.cluster health   # exit code 0 = every set has 1 primary and all members healthy
python -m lab.cluster init     # re-run initialisation (idempotent)
python -m lab.cluster down     # remove containers, keep data volumes
python -m lab.cluster reset    # down -v (incl. shard3), up, init  (empty cluster)
python -m lab.cluster reset --load              # FULL RESET incl. Atlas import + 4 collections (~6 min)
python -m lab.cluster reset --load --docs 1000000
```

## Data

```powershell
python -m lab.load atlas                 # Atlas sample_geospatial.shipwrecks -> lab.shipwrecks_ref (11,095 docs)
python -m lab.datagen --docs 500000      # preview generated docs + region distribution vs real data
python -m lab.load all --docs 500000     # create + shard + load the 4 collections (~1 min each)
python -m lab.load one ship_mono         # reload a single collection
python -m lab.load dist                  # per-shard owned / orphan docs and MB, chunks, jumbo
```

| Collection | Shard key | Expected behaviour |
|---|---|---|
| ship_mono | `{ts: 1}` | monotonic: all inserts hit the max-key chunk, so one hot shard |
| ship_lowcard | `{feature_type: 1}` | 5 values: chunks cannot split, so jumbo chunks |
| ship_hashed | `{_id: "hashed"}` | even spread, range queries scatter-gather |
| ship_compound | `{region: 1, recrd: 1}` | balanced and targeted by region |

500k docs ≈ 212 MB of BSON per collection (≈ 445 B/doc).

## Changes relative to the original `sample_geospatial.shipwrecks` dataset

The source is the Atlas sample collection (11,095 documents, 14 fields). What the lab uses, and every
way it differs from the source:

### Reference copy `lab.shipwrecks_ref`
* Copied document by document with pymongo: same 11,095 documents, same `_id` (ObjectId) and values.
* **Index difference:** Atlas has a `2dsphere` index on `coordinates`; the local copy has only `_id`
  (E4 creates indexes itself, to measure before/after).
* Stored unsharded on the `lab` database's primary shard.

### Generated collections `ship_mono`, `ship_lowcard`, `ship_hashed`, `ship_compound`
Profile of the source that the generator was built from (measured, not assumed):

| Field | Source distribution |
|---|---|
| feature_type | 5 values: Submerged-dangerous 59.3%, Visible 23.3%, Submerged-nondangerous 16.6%, `""` 0.6%, distributed remains 0.2% |
| watlev | 6 values, 75.7% "always under water/submerged" |
| quasou | 7 values, 50.2% "depth unknown", 27.4% `""` |
| sounding_type | 6 values, 98.7% `""` |
| gp_quality | `""` in 100% |
| recrd | **`""` in 100%** |
| chart | 2,201 distinct |
| vesslterms / history | 121 / 139 distinct, `""` in 98.9% / 96.7% |
| depth | 0 in 76.8%, range -1.2 to 1828.8, mixed int/float |
| coordinates | `[lon, lat]` legacy pair; 45% US East Coast, 33% Gulf of Mexico |

What the generator changes (`lab\datagen.py`, seed 42):

1. **Volume:** 500,000 documents per collection (parameter `--docs`) instead of 11,095. Each one is
   derived from a source document picked uniformly at random (≈45 copies per source document), so
   the source's geographic skew and value frequencies are reproduced, not invented.
2. **Copied unchanged** from the picked source document: `chart`, `feature_type`, `watlev`, `quasou`,
   `sounding_type`, `gp_quality`, `history`, `vesslterms`. The combination per document is a real one.
   Consequence: a vessel name that occurs once in the source occurs ~45 times in the generated set.
3. **Position jittered:** gaussian noise σ = 0.05° (≈5.5 km) on longitude and latitude; latitude
   clamped to ±89.9, longitude wrapped to [-180, 180), rounded to 6 decimals. `coordinates`,
   `latdec`, `londec` and `location` all hold the same jittered point (in the source,
   `latdec/londec` also equal `coordinates`).
4. **depth modified:** 0 stays 0; any other value is multiplied by a uniform factor 0.9-1.1 and rounded
   to 0.1, **floored at 0** (the source's few negative depths, minimum -1.2, become ≥ 0).
5. **recrd replaced (your decision):** the source value is `""` in every document, which would make
   the `{region, recrd}` shard key degenerate into `{region}` (~8 values, jumbo chunks). `recrd` is
   therefore a **synthetic record date** `"YYYYMMDD"`, uniform over 1970-01-01 … 2024-12-31
   (~20,000 distinct values), unrelated to the source.
6. **`_id` replaced:** a deterministic ObjectId built from `ts` and the sequence number (not the source
   `_id`), so all four collections contain byte-identical documents.
7. **Fields added** (not in the source):
   * `ts` — strictly increasing timestamp, 2025-01-01T00:00:00Z + i seconds (the monotonic shard key).
   * `region` — named sea area of the *source* point, from bounding boxes: `us_east_coast`,
     `gulf_of_mexico`, `us_west_coast`, `great_lakes`, `alaska_bering`, `caribbean`,
     `pacific_islands`, `other` (generated shares: 45.0 / 32.6 / 7.7 / 7.0 / 3.3 / 2.6 / 1.9 / 0.0 %).
     Because the box is assigned before jitter, a point within ~5 km of a box edge can lie just
     outside its region's box.
   * `location` — GeoJSON `{type: "Point", coordinates: [lon, lat]}` (the source has only the legacy pair).
8. Fields keep their source names and order; types are unchanged except `depth`, which is an int
   (0) or a float.

Additional collection `lab.probe` (used by `writer.py`) holds only small synthetic probe documents
`{k, seq, t, pad}` and no shipwreck data.

## Disk space on C:

Docker Desktop keeps all container data in one virtual disk:
`%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx`. Two things to know:

* **Lab data is not ephemeral.** The named volumes survive `down` and restarts and are deleted only by
  `python -m lab.cluster reset` or `docker compose down -v`. Expected size with the 1 GB oplog cap:
  ~1-1.5 GB per shard member and ~0.5 GB per config server.
* **The .vhdx only grows.** Space freed inside Docker (dropped collections, deleted volumes) is reused
  by Docker but is **not returned to Windows** until the file is compacted.

Each mongod's oplog is capped at 1 GB (`--oplogSize 1024`). The default is 5% of the disk, ~48 GB per
node inside Docker's VM, and it grew by ~212 MB per node with every 500k-doc load. Container logs
rotate at 10 MB × 2. For a cluster created before these limits:

```powershell
python -m lab.cluster shrink        # cap + compact the oplog of every running member (online)
docker system df                    # what Docker itself uses
```

To give the freed space back to Windows (stops Docker; needs an **administrator** PowerShell):

```powershell
# 1. quit Docker Desktop (tray icon -> Quit), then:
wsl --shutdown
diskpart
#   select vdisk file="C:\Users\<you>\AppData\Local\Docker\wsl\disk\docker_data.vhdx"
#   attach vdisk readonly
#   compact vdisk
#   detach vdisk
#   exit
# 2. start Docker Desktop again; the cluster containers stay stopped until:
python -m lab.cluster up
```

When the lab is finished, `docker compose -f docker-compose.yml -f docker-compose.shard3.yml down -v`
removes all lab data (then compact as above). `docker rmi mongo:8.0` frees another 1.3 GB.

## Exporting results as images / PDF

Every experiment saves its console output to `results\eNN\console.txt` and exports automatically.
To re-export:

```powershell
python -m lab.export e01        # or: python -m lab.export all
```

This produces `results\eNN\img\*.png` (one terminal-style image per section, plus one per small CSV,
plus any charts) and `results\eNN\eNN_report.pdf` (all images, one per page).

## Tools used by the experiments (also runnable on their own)

```powershell
python -m lab.writer --duration 60 --out results\tmp\writer.csv     # 1 insert / 100 ms via mongos, retryWrites=False
python -m lab.writer --shard shard1RS --duration 60                 # only to shard1RS
python -m lab.metrics --interval 5 --duration 60 --out results\tmp\metrics   # dist / opcounters / balancer / docker CSVs
python -m lab.chaos primary shard1RS                                # who is primary now
python -m lab.chaos kill s1a | stop s1a | start s1a | pause s1a | unpause s1a
python -m lab.chaos disconnect s1a | connect s1a
python -m lab.chaos restore                                         # undo all faults
```

`lab.probe` (writer target) is sharded on `{k: 1}` and split at `k = 0`: `k < 0` lives on shard1RS and
`k >= 0` on shard2RS, with balancing disabled for it. The writer alternates between the two, so a
failure on one shard shows as errors on that shard only.

## Experiments

| # | Command | Output |
|---|---|---|
| E0 | `python experiments\e00_environment.py` (environment facts + architecture diagram, chapter 3) | `results\e00\` |
| E1 | `python experiments\e01_cluster_survey.py` (+ topology diagram) | `results\e01\` |
| E2 | `python experiments\e02_shard_keys.py` (~20 min; `--docs`, `--phases off on`, `--colls`); `--replot` rebuilds summary/charts/chunk map from saved data | `results\e02\` |
| E3 | `python experiments\e03_query_routing.py` (~1 min; needs the E2 collections) | `results\e03\` |

## Layout

```
docker-compose.yml          base cluster
docker-compose.shard3.yml   third shard (E9)
lab\common.py               topology constants, docker/mongosh/pymongo helpers
lab\cluster.py              lifecycle: up / init / status / health / reset / shard3
lab\datagen.py              synthetic document generator (sampled from the real data)
lab\load.py                 Atlas import, shardCollection, bulk insert, distribution
lab\export.py               console/CSV -> PNG images + PDF
lab\writer.py               write probe (CSV: timestamp, ok/error, error type, latency)
lab\metrics.py              5 s sampler: per-shard MB/docs, opcounters, balancer, docker stats
lab\chaos.py                kill / stop / pause / disconnect / netem wrappers + restore
lab\plots.py                shared chart style (fixed colour per shard)
lab\diagram.py              cluster diagrams from observed state (topology / per-step / query routing)
experiments\eNN_*.py        one script per experiment
results\eNN\                CSV / JSON / TXT output per experiment
```
