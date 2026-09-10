#!/usr/bin/env python
"""
Standalone RealSense D455 verification — run in Terminal directly,
NOT through Claude Code or any wrapper, so TCC attribution goes to
Terminal (which has Camera permission).

Usage (in Terminal, after sourcing the venv):
    source .venv/bin/activate
    python tools/test_realsense.py
"""
import sys
import numpy as np


def main() -> int:
    try:
        import pyrealsense2 as rs
    except ImportError as e:
        print(f"[FAIL] pyrealsense2 not importable: {e}")
        return 1

    print("[1] enumerate devices")
    ctx = rs.context()
    n = ctx.devices.size()
    print(f"    devices = {n}")
    if n == 0:
        print("[FAIL] no RealSense device connected")
        return 2
    for i in range(n):
        d = ctx.devices[i]
        print(f"    name   : {d.get_info(rs.camera_info.name)}")
        print(f"    serial : {d.get_info(rs.camera_info.serial_number)}")
        print(f"    fw     : {d.get_info(rs.camera_info.firmware_version)}")
        print(f"    usb    : {d.get_info(rs.camera_info.usb_type_descriptor)}")

    print("[2] start pipeline — color 640x480 + depth 848x480 @ 30Hz")
    pipeline = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)
    profile = pipeline.start(cfg)
    scale = profile.get_device().first_depth_sensor().get_depth_scale()
    print(f"    depth scale = {scale} m/unit")

    align = rs.align(rs.stream.color)
    print("[3] grab 20 frames")
    try:
        for i in range(20):
            frames = pipeline.wait_for_frames(timeout_ms=5000)
            frames = align.process(frames)
            c = frames.get_color_frame()
            d = frames.get_depth_frame()
            if not c or not d:
                print(f"    [{i}] missing stream")
                continue
            depth_arr = np.asanyarray(d.get_data())
            valid = depth_arr[depth_arr > 0]
            med = float(np.median(valid)) * scale if valid.size else float("nan")
            print(
                f"    [{i:02d}] color {c.get_width()}x{c.get_height()}, "
                f"depth {d.get_width()}x{d.get_height()}, "
                f"valid={valid.size}, median={med:.3f}m"
            )
    finally:
        pipeline.stop()

    print("[OK] D455 streaming verified — depth pipeline end-to-end working")
    return 0


if __name__ == "__main__":
    sys.exit(main())
