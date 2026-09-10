#!/usr/bin/env python
"""
Multi-Camera Room Monitoring System - Main Entry Point
Supports visual monitoring, event recording, and LLM analysis

Usage:
    python main.py                    # Run basic monitoring
    python main.py --api              # Run with API service
    python main.py --api --port 8000  # Use custom port
"""
import argparse
import logging
import sys
import os
import threading
import time

# Load .env before anything else
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add project path
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

from src.core.room_monitor import RoomMonitor
from src.core.boot_selftest import run_boot_selftest, SelfTestError
from src.services.api_server import RoomMonitorAPI
import src.utils.config as _cfg
from src.utils.config import ZONE_CONFIG


def setup_logging():
    """Set up logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('room_monitor.log', encoding='utf-8')
        ]
    )


def main():
    """Main program entry point"""
    parser = argparse.ArgumentParser(
        description="Multi-Camera Room Monitoring System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Basic monitoring
  python main.py --api              # Enable REST API
  python main.py --api --port 8000  # Custom API port
  python main.py --demo             # Demo mode (visualization)
    """
    )

    parser.add_argument(
        "--api",
        action="store_true",
        help="Start REST API service"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="API service port (default 5000)"
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="API bind address (default 0.0.0.0)"
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Demo mode (show visualization window)"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug mode"
    )

    parser.add_argument(
        "--agent-url",
        type=str,
        default=None,
        help="Agent server URL (overrides config, e.g. http://localhost:8000)"
    )

    parser.add_argument(
        "--no-agent",
        action="store_true",
        help="Disable event forwarding to agent server"
    )

    parser.add_argument(
        "--interaction-distance",
        type=int,
        default=None,
        help="Override interaction zone distance in pixels (default from config)"
    )

    parser.add_argument(
        "--enable-vlm",
        action="store_true",
        help="Enable VLM visual observations (requires OPENAI_API_KEY)"
    )

    parser.add_argument(
        "--skip-selftest",
        action="store_true",
        help="Skip boot self-test (dev/demo only — production should never use this)"
    )

    parser.add_argument(
        "--camera-device",
        type=int,
        default=None,
        help=(
            "Override camera source to a UVC device index (e.g. 0, 1, 2). "
            "Overrides whatever 'source' is set in CAMERAS config for the "
            "first camera entry. Use this to switch between FaceTime/RealSense "
            "without editing config.py."
        ),
    )

    args = parser.parse_args()

    # Set up logging
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("="*60)
    logger.info("Multi-Camera Room Monitoring System Starting")
    logger.info("="*60)

    # Apply CLI overrides to config before constructing monitor
    if args.no_agent:
        _cfg.AGENT_SERVER_CONFIG["enabled"] = False
        logger.info("Agent server forwarding disabled (--no-agent)")
    if args.agent_url:
        _cfg.AGENT_SERVER_CONFIG["url"] = args.agent_url
        logger.info(f"Agent server URL overridden: {args.agent_url}")
    if args.interaction_distance is not None:
        _cfg.ZONE_CONFIG["interaction_distance"] = args.interaction_distance
        logger.info(f"Interaction zone distance overridden: {args.interaction_distance}px")
    if args.enable_vlm:
        _cfg.VLM_CONFIG["enabled"] = True
        logger.info("VLM observations enabled")
    if args.camera_device is not None:
        # Override source of the FIRST camera entry. For multi-camera
        # deployments, editing config directly is still the right path.
        first_cam_id = next(iter(_cfg.CAMERAS))
        _cfg.CAMERAS[first_cam_id]["source"] = args.camera_device
        logger.info(
            "Camera source overridden: %s.source = %s",
            first_cam_id, args.camera_device,
        )

    # ---------- Boot self-test ----------
    # In production we run with a watchdog and no one tailing logs. Any broken
    # dependency must surface at startup with a non-zero exit code so the
    # watchdog's circuit breaker eventually trips and a human gets notified
    # (because the avatar stops responding to demos).
    if not args.skip_selftest:
        logger.info("Running boot self-test...")
        try:
            run_boot_selftest(strict=True)
            logger.info("Boot self-test passed")
        except SelfTestError as e:
            logger.critical(f"BOOT SELFTEST FAILED: {e}")
            logger.critical("Refusing to start — fix the failing dependency and retry.")
            logger.critical("Use --skip-selftest to bypass (dev only, NOT for production).")
            return 2

    try:
        # Create monitoring system
        logger.info("Initializing monitoring system...")
        monitor = RoomMonitor()

        # Start API service (if specified)
        monitor_thread = None
        api_thread = None

        if args.api:
            logger.info(f"Starting REST API service: {args.host}:{args.port}")
            api = RoomMonitorAPI(
                monitor,
                host=args.host,
                port=args.port,
                debug=args.debug
            )
            api_thread = api.run_threaded()

            logger.info(f"API started, access at: http://localhost:{args.port}")
            logger.info(f"Web Dashboard: http://localhost:{args.port}/")
            logger.info(f"API Docs: http://localhost:{args.port}/api")
            logger.info("Open the dashboard and click Start to begin monitoring.")

            # Main thread stays alive, waiting for Ctrl+C
            # Monitor is started via the web UI's Start button (/api/control/start)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Interrupt signal received, shutting down...")
        else:
            # No API, run monitoring system directly (blocking)
            logger.info("Starting room monitor...")
            monitor.run(demo_mode=args.demo)

    except KeyboardInterrupt:
        logger.info("Interrupt signal received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1

    logger.info("="*60)
    logger.info("System shut down")
    logger.info("="*60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
