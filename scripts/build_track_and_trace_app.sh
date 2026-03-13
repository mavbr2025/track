#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/artifacts/build/track-and-trace-app"
ICONSET_DIR="$BUILD_DIR/TrackAndTrace.iconset"
BASE_PNG="$BUILD_DIR/track_and_trace_1024.png"
APP_NAME="MTM Track and Trace.app"
APP_PATH="$ROOT_DIR/$APP_NAME"
ICON_NAME="TrackAndTrace"
ICON_PATH="$APP_PATH/Contents/Resources/$ICON_NAME.icns"

set_or_add_plist_key() {
  local plist_path="$1"
  local key="$2"
  local value="$3"

  if /usr/libexec/PlistBuddy -c "Print :$key" "$plist_path" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$plist_path" >/dev/null
  else
    /usr/libexec/PlistBuddy -c "Add :$key string $value" "$plist_path" >/dev/null
  fi
}

rm -rf "$BUILD_DIR" "$APP_PATH"
mkdir -p "$BUILD_DIR" "$ICONSET_DIR"

python3 - "$BASE_PNG" <<'PY'
import math
import struct
import sys
import zlib

output_path = sys.argv[1]
w = h = 1024
bg = (17, 29, 109, 255)
fg = (245, 247, 251, 255)
accent = (141, 205, 255, 255)
shadow = (8, 16, 66, 255)

pixels = bytearray(bg * (w * h))


def set_px(x, y, color):
    if 0 <= x < w and 0 <= y < h:
        idx = (y * w + x) * 4
        pixels[idx:idx + 4] = bytes(color)


def blend_px(x, y, color, alpha):
    if alpha <= 0 or not (0 <= x < w and 0 <= y < h):
        return
    if alpha >= 1:
        set_px(x, y, color)
        return
    idx = (y * w + x) * 4
    base = pixels[idx:idx + 4]
    out = bytearray(4)
    for i in range(3):
        out[i] = int(base[i] * (1 - alpha) + color[i] * alpha)
    out[3] = 255
    pixels[idx:idx + 4] = out


def draw_circle(cx, cy, radius, color):
    x0 = max(0, int(cx - radius - 1))
    x1 = min(w - 1, int(cx + radius + 1))
    y0 = max(0, int(cy - radius - 1))
    y1 = min(h - 1, int(cy + radius + 1))
    rr = radius * radius
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            dist = dx * dx + dy * dy
            if dist <= rr:
                set_px(x, y, color)


def draw_ring(cx, cy, radius, thickness, color):
    outer = radius
    inner = radius - thickness
    x0 = max(0, int(cx - outer - 1))
    x1 = min(w - 1, int(cx + outer + 1))
    y0 = max(0, int(cy - outer - 1))
    y1 = min(h - 1, int(cy + outer + 1))
    outer2 = outer * outer
    inner2 = max(0, inner * inner)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            dist = dx * dx + dy * dy
            if inner2 <= dist <= outer2:
                set_px(x, y, color)


def draw_rounded_rect(x0, y0, x1, y1, radius, color):
    r2 = radius * radius
    for y in range(max(0, y0), min(h, y1)):
        for x in range(max(0, x0), min(w, x1)):
            inside = False
            if x0 + radius <= x < x1 - radius or y0 + radius <= y < y1 - radius:
                inside = True
            else:
                corners = (
                    (x0 + radius, y0 + radius),
                    (x1 - radius - 1, y0 + radius),
                    (x0 + radius, y1 - radius - 1),
                    (x1 - radius - 1, y1 - radius - 1),
                )
                for cx, cy in corners:
                    dx = x + 0.5 - cx
                    dy = y + 0.5 - cy
                    if dx * dx + dy * dy <= r2:
                        inside = True
                        break
            if inside:
                set_px(x, y, color)


def bezier(p0, p1, p2, t):
    u = 1 - t
    x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
    y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
    return x, y


def draw_bezier_tube(p0, p1, p2, radius, color, steps=160):
    for i in range(steps + 1):
        t = i / steps
        x, y = bezier(p0, p1, p2, t)
        draw_circle(x, y, radius, color)


def draw_triangle(points, color):
    (x1, y1), (x2, y2), (x3, y3) = points
    min_x = max(0, int(min(x1, x2, x3)))
    max_x = min(w - 1, int(max(x1, x2, x3)))
    min_y = max(0, int(min(y1, y2, y3)))
    max_y = min(h - 1, int(max(y1, y2, y3)))
    denom = ((y2 - y3)*(x1 - x3) + (x3 - x2)*(y1 - y3))
    if denom == 0:
        return
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            px = x + 0.5
            py = y + 0.5
            a = ((y2 - y3)*(px - x3) + (x3 - x2)*(py - y3)) / denom
            b = ((y3 - y1)*(px - x3) + (x1 - x3)*(py - y3)) / denom
            c = 1 - a - b
            if a >= 0 and b >= 0 and c >= 0:
                set_px(x, y, color)


# soft background arcs
for cx, cy, rad in ((-40, 140, 320), (860, 120, 260), (110, 1020, 300), (930, 900, 260)):
    draw_ring(cx, cy, rad, 4, (255, 255, 255, 95))

# shadow plate
draw_rounded_rect(192, 250, 832, 736, 96, shadow)

# shipment container outline
draw_rounded_rect(180, 238, 820, 724, 96, fg)
draw_rounded_rect(228, 286, 772, 676, 70, bg)

# container ribs
for x in (335, 442, 549, 656):
    draw_rounded_rect(x, 340, x + 20, 624, 10, fg)

# tracking route
draw_bezier_tube((292, 780), (456, 632), (588, 764), 32, accent, steps=140)
draw_bezier_tube((588, 764), (680, 846), (806, 758), 32, accent, steps=100)
draw_circle(285, 790, 44, fg)
draw_circle(286, 790, 18, bg)
draw_circle(806, 758, 30, fg)

# location pin
pin_cx, pin_cy = 770, 246
draw_circle(pin_cx, pin_cy, 86, fg)
draw_circle(pin_cx, pin_cy, 42, bg)
draw_triangle(((770, 378), (720, 290), (820, 290)), fg)
draw_circle(pin_cx, pin_cy, 18, accent)

# magnifier lens
draw_ring(330, 330, 86, 26, fg)
draw_rounded_rect(380, 392, 486, 432, 18, fg)

# export as PNG
raw = bytearray()
stride = w * 4
for y in range(h):
    raw.append(0)
    start = y * stride
    raw.extend(pixels[start:start + stride])


def chunk(tag, data):
    return (
        struct.pack("!I", len(data)) +
        tag +
        data +
        struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


png = bytearray(b"\x89PNG\r\n\x1a\n")
png.extend(chunk(b"IHDR", struct.pack("!IIBBBBB", w, h, 8, 6, 0, 0, 0)))
png.extend(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
png.extend(chunk(b"IEND", b""))

with open(output_path, "wb") as fh:
    fh.write(png)
PY

for size in 16 32 64 128 256 512; do
  sips -z "$size" "$size" "$BASE_PNG" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
done
sips -z 32 32 "$BASE_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 64 64 "$BASE_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 256 256 "$BASE_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 512 512 "$BASE_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 1024 1024 "$BASE_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET_DIR" -o "$BUILD_DIR/$ICON_NAME.icns"

cat >"$BUILD_DIR/launcher.applescript" <<'APPLESCRIPT'
set appPath to POSIX path of (path to me)
set projectDir to do shell script "dirname " & quoted form of appPath
set commandFile to projectDir & "/Run Shipment Sync Now.command"
do shell script "open -a Terminal " & quoted form of commandFile
APPLESCRIPT

osacompile -o "$APP_PATH" "$BUILD_DIR/launcher.applescript" >/dev/null
cp "$BUILD_DIR/$ICON_NAME.icns" "$ICON_PATH"
set_or_add_plist_key "$APP_PATH/Contents/Info.plist" "CFBundleName" "MTM Track and Trace"
set_or_add_plist_key "$APP_PATH/Contents/Info.plist" "CFBundleDisplayName" "MTM Track and Trace"
set_or_add_plist_key "$APP_PATH/Contents/Info.plist" "CFBundleIconFile" "$ICON_NAME"
set_or_add_plist_key "$APP_PATH/Contents/Info.plist" "CFBundleIconName" "$ICON_NAME"
touch "$APP_PATH"

echo "Created app bundle:"
echo "$APP_PATH"
echo
echo "Created base icon:"
echo "$BASE_PNG"
