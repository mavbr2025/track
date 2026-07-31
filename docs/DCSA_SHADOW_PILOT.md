# DCSA Shadow Pilot

## Purpose

The DCSA shadow lane captures and validates official carrier event payloads
without changing operational Track & Trace behavior. It is separate from the
normal `shipment-sync` command and has no ClickUp update, comment, field-write,
or native-status-write path.

It is an evidence lane, not a second production tracker.

## Scope

- Pilot carriers: CMA CGM and Maersk.
- CMA CGM is pinned to its approved TNT 2.2 `GET /events` contract.
- Maersk is admitted only after a captured payload establishes its TNT version.
- A browser, scraped page, public tracking response, or `API-Version` header is
  not evidence of a DCSA TNT version.
- Existing carrier ECS schedules stay unchanged.

## Command and safety boundary

```bash
# Parses configuration only. It makes no external request.
dcsa-tnt-shadow

# Reads ClickUp inventory and official carrier events, then records validated
# evidence in the configured ledger. It never writes to ClickUp.
dcsa-tnt-shadow --run
```

`--run` is enabled only with all required explicit configuration. A disabled or
partially configured shadow lane exits without accessing ClickUp or a carrier.

## Required configuration

```dotenv
DCSA_TNT_SHADOW_ENABLED=true
DCSA_TNT_SHADOW_CARRIERS=cma cgm
DCSA_TNT_SHADOW_CMA_CGM_VERSION=2.2
DCSA_TNT_SHADOW_MAX_SHIPMENTS=25

# Local validation only
DCSA_TNT_LEDGER_BACKEND=sqlite
DCSA_TNT_LEDGER_DB_PATH=/tmp/dcsa-events.sqlite3

# ECS production shadow worker only
# DCSA_TNT_LEDGER_BACKEND=dynamodb
# DCSA_TNT_LEDGER_TABLE=track-trace-dcsa-events
```

For Maersk, add `maersk` to `DCSA_TNT_SHADOW_CARRIERS` only after an approved
captured response establishes the contract, then set
`DCSA_TNT_SHADOW_MAERSK_VERSION=2.2` or `2.3`. The source requires
`MAERSK_API_MODE=events` and `MAERSK_TRACKING_API_URL`; it rejects automatic
mode and all website fallbacks.

## Evidence ledger

Production evidence belongs in DynamoDB, never an ECS task filesystem. The
ledger stores a redacted raw payload plus a normalized record and uses a
conditional event-key insert for idempotency.

The infrastructure template creates:

- `track-trace-dcsa-events`, a pay-per-request DynamoDB table with point-in-time
  recovery, server-side encryption, service-level deletion protection, and
  carrier/task indexes;
- `TrackTraceDcsaShadowTaskRole`, a dedicated role that can query, read, write,
  and update only that ledger; it has no ClickUp or scheduler permissions;
- a dedicated CloudWatch log group with 30-day retention.

Provision it only from an authorized AWS session:

```bash
scripts/provision_dcsa_shadow_ledger.sh
```

The template retains the ledger and log group on stack deletion. It grants no
delete or scan permissions.

## Run acceptance gates

Before a scheduled shadow task is created, demonstrate all of the following:

1. A carrier response is validated against the declared TNT version with no
   malformed-event leakage into the ledger.
2. Re-running the same response creates no duplicate event evidence.
3. The ledger contains the correct carrier, task identity, source endpoint,
   event time, event code, and redacted payload.
4. The summary confirms zero ClickUp writes; a direct ClickUp task readback
   shows no field, native-status, or comment change.
5. CMA and Maersk deltas are reviewed against the existing carrier path before
   any projection design is implemented.

`PENC` remains `requires_review`; `CANC` remains `halted`. Neither condition
activates a ClickUp change during this pilot.
