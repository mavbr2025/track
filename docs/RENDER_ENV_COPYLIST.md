# Render Env Copy List

Use this for the `shipment-api` web service in Render.

## Add exactly as shown

```text
SHIPMENT_API_HOST=0.0.0.0
SHIPMENT_PREFLIGHT_ENABLED=true
SHIPMENT_MIN_SYNC_INTERVAL_HOURS=2
CLICKUP_DISCOVER_LISTS_FROM_SPACES=true
MSC_USE_PLAYWRIGHT=true
MSC_PLAYWRIGHT_REQUIRED=true
MSC_PLAYWRIGHT_BROWSER=chromium
MSC_PLAYWRIGHT_CHANNEL=
MSC_PLAYWRIGHT_HEADLESS=true
MSC_PLAYWRIGHT_TRACKING_URL=https://www.msc.com/en/track-a-shipment
MSC_PLAYWRIGHT_API_ENDPOINT=https://www.msc.com/api/feature/tools/TrackingInfo
```

## Copy from your local `.env`

Enter the same values you already use locally for these:

```text
CLICKUP_API_TOKEN=<copy local value>
or
CLICKUP_OAUTH_ACCESS_TOKEN=<copy local value>

CLICKUP_LIST_ID=<copy local value>
CLICKUP_SPACE_IDS=<copy local value>
CLICKUP_CF_CONTAINER_NO=<copy local value>
CLICKUP_CF_BOOKING_NO=<copy local value>
CLICKUP_CF_SHIPPING_LINE=<copy local value>
```

## Create a new value in Render

```text
SHIPMENT_API_TRIGGER_TOKEN=<new long random secret>
```

## Optional for later

These are blank locally and can wait until after the first production test:

```text
CLICKUP_LIST_IDS=
CLICKUP_CF_SHIPMENT_STATUS=
CLICKUP_CF_STATUS_LAST_CHECKED=
```

## Cron job

For `track-trace-cron`, use the same env vars as `shipment-api`.
