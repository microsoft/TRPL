"""
Zone Labeler — click on a screenshot with the mouse to label the
entry_zone / mic_zone polygons.

Usage:
    python tools/zone_labeler.py <image_path>
    python tools/zone_labeler.py test_data/frame.jpg

Instructions:
    1. When the window opens, first label entry_zone (the door / entrance area)
    2. Left-click each vertex of the polygon
    3. Press Enter to confirm the current zone; it auto-switches to mic_zone
    4. Keep clicking to label mic_zone (the microphone area)
    5. Press Enter to confirm
    6. The printed output can be pasted directly into ZONE_CONFIG in config.py

Keyboard shortcuts:
    Left click   — add a vertex
    Right click  — undo the last vertex
    Enter        — confirm the current zone
    r            — redo the current zone
    q / ESC      — quit
"""
import sys
import cv2
import numpy as np

ZONE_NAMES = ["entry_zone", "mic_zone"]
ZONE_COLORS = {
    "entry_zone": (0, 200, 255),   # Orange
    "mic_zone": (255, 100, 255),   # Magenta
}


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/zone_labeler.py <image_path>")
        print("  e.g. python tools/zone_labeler.py test_data/frame.jpg")
        sys.exit(1)

    image_path = sys.argv[1]
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: cannot read image '{image_path}'")
        sys.exit(1)

    h, w = img.shape[:2]
    print(f"Image loaded: {w}x{h}")
    print(f"Click polygon vertices for each zone. Press Enter to confirm.\n")

    results = {}
    current_points = []
    zone_idx = 0

    def draw():
        vis = img.copy()
        # Draw completed zones
        for name, pts in results.items():
            color = ZONE_COLORS.get(name, (200, 200, 200))
            pts_px = [(int(x * w), int(y * h)) for x, y in pts]
            pts_arr = np.array(pts_px, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis, [pts_arr], True, color, 2)
            overlay = vis.copy()
            cv2.fillPoly(overlay, [pts_arr], (*color, 80))
            cv2.addWeighted(overlay, 0.3, vis, 0.7, 0, vis)
            cv2.putText(vis, name, (pts_px[0][0] + 5, pts_px[0][1] - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        # Draw current zone being labeled
        if zone_idx < len(ZONE_NAMES):
            name = ZONE_NAMES[zone_idx]
            color = ZONE_COLORS.get(name, (200, 200, 200))
            for i, pt in enumerate(current_points):
                px, py = int(pt[0] * w), int(pt[1] * h)
                cv2.circle(vis, (px, py), 6, color, -1)
                cv2.putText(vis, str(i), (px + 8, py - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                if i > 0:
                    prev = current_points[i - 1]
                    cv2.line(vis, (int(prev[0] * w), int(prev[1] * h)),
                            (px, py), color, 2)
            if len(current_points) > 2:
                first = current_points[0]
                last = current_points[-1]
                cv2.line(vis, (int(last[0] * w), int(last[1] * h)),
                        (int(first[0] * w), int(first[1] * h)), color, 1)

            # Status bar
            cv2.rectangle(vis, (0, h - 40), (w, h), (40, 40, 40), -1)
            status = f"Labeling: {name} | Points: {len(current_points)} | Enter=confirm, R=redo, Right-click=undo"
            cv2.putText(vis, status, (10, h - 12),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        else:
            cv2.rectangle(vis, (0, h - 40), (w, h), (40, 40, 40), -1)
            cv2.putText(vis, "All zones labeled! Press any key to see output.",
                       (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        return vis

    def on_mouse(event, x, y, flags, param):
        nonlocal current_points
        if zone_idx >= len(ZONE_NAMES):
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            nx = round(x / w, 4)
            ny = round(y / h, 4)
            current_points.append((nx, ny))
            print(f"  Point {len(current_points)-1}: pixel=({x}, {y}) → normalized=({nx}, {ny})")
            cv2.imshow("Zone Labeler", draw())
        elif event == cv2.EVENT_RBUTTONDOWN:
            if current_points:
                removed = current_points.pop()
                print(f"  Undo: removed ({removed[0]}, {removed[1]})")
                cv2.imshow("Zone Labeler", draw())

    cv2.namedWindow("Zone Labeler", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Zone Labeler", min(w, 1400), min(h, 900))
    cv2.setMouseCallback("Zone Labeler", on_mouse)

    while zone_idx <= len(ZONE_NAMES):
        cv2.imshow("Zone Labeler", draw())
        key = cv2.waitKey(0) & 0xFF

        if key == 27 or key == ord('q'):  # ESC or q
            break

        if key == 13 or key == 10:  # Enter
            if zone_idx < len(ZONE_NAMES):
                name = ZONE_NAMES[zone_idx]
                if len(current_points) >= 3:
                    results[name] = list(current_points)
                    print(f"\n✓ {name} saved with {len(current_points)} vertices\n")
                    current_points = []
                    zone_idx += 1
                    if zone_idx < len(ZONE_NAMES):
                        print(f"Now label: {ZONE_NAMES[zone_idx]}")
                else:
                    print(f"  Need at least 3 points (currently {len(current_points)})")
            else:
                break

        if key == ord('r'):  # Redo current zone
            current_points = []
            print(f"  Reset {ZONE_NAMES[zone_idx]}")

    cv2.destroyAllWindows()

    if not results:
        print("No zones labeled.")
        return

    # Output
    print("\n" + "=" * 60)
    print("Copy this into config.py ZONE_CONFIG:")
    print("=" * 60)
    print("ZONE_CONFIG = {")
    for name, pts in results.items():
        print(f'    "{name}": [')
        for x, y in pts:
            print(f"        ({x}, {y}),")
        print("    ],")
    print("}")
    print("=" * 60)


if __name__ == "__main__":
    main()
