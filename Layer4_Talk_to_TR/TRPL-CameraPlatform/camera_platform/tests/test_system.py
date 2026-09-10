#!/usr/bin/env python
"""
Unit tests and integration tests
"""
import sys
import os
import unittest
import numpy as np
import time

# Add project root directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.event_manager import EventManager, Event, EventType, PersonState
from src.core.hand_detector import HandDetector


class TestEventManager(unittest.TestCase):
    """Test event management system"""

    def setUp(self):
        self.manager = EventManager()
        self.manager.register_camera("camera_0")

    def test_person_creation(self):
        """Test person creation"""
        person_id = self.manager.get_or_create_person("camera_0", 1)
        self.assertEqual(person_id, 0)

        person_id2 = self.manager.get_or_create_person("camera_0", 1)
        self.assertEqual(person_id2, 0)  # Should return the same ID

    def test_person_state(self):
        """Test person state"""
        person_id = self.manager.get_or_create_person("camera_0", 1)

        self.manager.update_person_seen(person_id, "camera_0", (100, 100))

        person = self.manager.get_person_state(person_id)
        self.assertIsNotNone(person)
        self.assertEqual(person.person_id, person_id)
        self.assertIn("camera_0", person.cameras_seen)

    def test_event_recording(self):
        """Test event recording"""
        event = Event(
            event_type=EventType.HAND_RAISED,
            camera_id="camera_0",
            person_id=0,
            data={"side": "right"}
        )

        self.manager.record_event(event)

        events = self.manager.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.HAND_RAISED)

    def test_event_filtering(self):
        """Test event filtering"""
        self.manager.record_event(Event(
            event_type=EventType.HAND_RAISED,
            person_id=0
        ))
        self.manager.record_event(Event(
            event_type=EventType.PERSON_ENTERED,
            person_id=1
        ))

        raised_events = self.manager.get_events(event_type=EventType.HAND_RAISED)
        self.assertEqual(len(raised_events), 1)

        person0_events = self.manager.get_events(person_id=0)
        self.assertEqual(len(person0_events), 1)

    def test_room_summary(self):
        """Test room summary"""
        person_id = self.manager.get_or_create_person("camera_0", 1)
        self.manager.update_person_seen(person_id, "camera_0")

        event = Event(
            event_type=EventType.PERSON_ENTERED,
            camera_id="camera_0",
            person_id=person_id
        )
        self.manager.record_event(event)

        summary = self.manager.get_room_summary()

        self.assertIn("timestamp", summary)
        self.assertIn("total_persons_in_room", summary)
        self.assertGreaterEqual(summary["total_persons_in_room"], 0)


class TestHandDetector(unittest.TestCase):
    """Test hand raise detection"""

    def setUp(self):
        self.detector = HandDetector()

    def test_angle_calculation(self):
        """Test angle calculation"""
        a = np.array([0, 0])
        b = np.array([1, 0])
        c = np.array([1, 1])

        angle = HandDetector.angle_degrees(a, b, c)
        self.assertAlmostEqual(angle, 90, delta=1)

    def test_detect_hand_raise_interface(self):
        """Test detect_hand_raise interface return format"""
        kpts_xy = np.zeros((17, 2), dtype=np.float32)
        kpts_conf = np.ones(17, dtype=np.float32) * 0.9

        # Set up basic skeleton
        kpts_xy[5] = [50, 200]   # Left shoulder
        kpts_xy[6] = [150, 200]  # Right shoulder
        kpts_xy[11] = [60, 400]  # Left hip
        kpts_xy[12] = [140, 400] # Right hip
        kpts_xy[8] = [160, 250]  # Right elbow
        kpts_xy[10] = [170, 350] # Right wrist (below shoulder)

        left, right, details = self.detector.detect_hand_raise(kpts_xy, kpts_conf)

        self.assertIsInstance(left, bool)
        self.assertIsInstance(right, bool)
        self.assertIn("left", details)
        self.assertIn("right", details)
        self.assertIn("score", details["right"])

    def test_hand_raised_with_low_confidence(self):
        """Test that low confidence should not detect hand raise"""
        kpts_xy = np.zeros((17, 2), dtype=np.float32)
        kpts_conf = np.ones(17, dtype=np.float32) * 0.01  # Very low confidence

        kpts_xy[6] = [100, 200]
        kpts_xy[10] = [100, 50]

        left, right, details = self.detector.detect_hand_raise(kpts_xy, kpts_conf)
        self.assertFalse(right)


class TestIntegration(unittest.TestCase):
    """Integration tests"""

    def test_full_workflow(self):
        """Test complete workflow"""
        manager = EventManager()
        manager.register_camera("camera_0")
        detector = HandDetector()

        # Simulate person entering
        person_id = manager.get_or_create_person("camera_0", 1)
        manager.update_person_seen(person_id, "camera_0", (100, 100))

        enter_event = Event(
            event_type=EventType.PERSON_ENTERED,
            camera_id="camera_0",
            person_id=person_id
        )
        manager.record_event(enter_event)

        # Simulate hand raise detection (construct full skeleton)
        kpts_xy = np.zeros((17, 2), dtype=np.float32)
        kpts_conf = np.ones(17, dtype=np.float32) * 0.9
        kpts_xy[5] = [50, 200]
        kpts_xy[6] = [150, 200]
        kpts_xy[11] = [60, 400]
        kpts_xy[12] = [140, 400]
        kpts_xy[8] = [155, 100]
        kpts_xy[10] = [160, 50]

        left, right, _ = detector.detect_hand_raise(kpts_xy, kpts_conf)

        if left or right:
            side = "both" if (left and right) else ("left" if left else "right")
            raise_event = Event(
                event_type=EventType.HAND_RAISED,
                camera_id="camera_0",
                person_id=person_id,
                data={"side": side}
            )
            manager.record_event(raise_event)

        # Verify results
        events = manager.get_events()
        self.assertGreaterEqual(len(events), 1)

        summary = manager.get_room_summary()
        self.assertGreater(summary["total_persons_in_room"], 0)


if __name__ == "__main__":
    unittest.main()
