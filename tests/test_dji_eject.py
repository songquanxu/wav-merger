import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wav_merger import DjiMicVolume, WavMergerApp, disk_info_is_dji_mic, path_is_within


class DjiMicDetectionTests(unittest.TestCase):
    def make_dji_layout(self, root: Path) -> None:
        recording = root / "TX_MIC001_20260828_140800"
        recording.mkdir()
        (recording / "TX00_MIC001_20260828_140800_orig.wav").touch()

    def test_recognizes_external_mic_transmitter_with_dji_layout(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            mount_point = Path(folder)
            self.make_dji_layout(mount_point)
            info = {
                "Internal": False,
                "Removable": True,
                "IORegistryEntryName": "Wireless Mic Tx Media",
                "MediaName": "Mic Tx",
            }
            self.assertTrue(disk_info_is_dji_mic(info, mount_point))

    def test_rejects_ordinary_external_drive(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            mount_point = Path(folder)
            self.make_dji_layout(mount_point)
            info = {"Internal": False, "Removable": True, "MediaName": "USB Flash Drive"}
            self.assertFalse(disk_info_is_dji_mic(info, mount_point))

    def test_rejects_similarly_named_drive_without_dji_layout(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            info = {"Internal": False, "Removable": True, "VolumeName": "DJI MIC"}
            self.assertFalse(disk_info_is_dji_mic(info, Path(folder)))

    def test_rejects_internal_volume(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            mount_point = Path(folder)
            self.make_dji_layout(mount_point)
            info = {"Internal": True, "Removable": False, "VolumeName": "DJI MIC"}
            self.assertFalse(disk_info_is_dji_mic(info, mount_point))

    def test_path_membership_does_not_confuse_name_prefixes(self) -> None:
        self.assertTrue(path_is_within(Path("/Volumes/DJI/recording.wav"), Path("/Volumes/DJI")))
        self.assertFalse(path_is_within(Path("/Volumes/DJI-copy/recording.wav"), Path("/Volumes/DJI")))

    def test_eject_worker_uses_captured_whole_disk_identifier(self) -> None:
        app = WavMergerApp.__new__(WavMergerApp)
        app.work_queue = queue.Queue()
        app.disk_info = lambda _target: {
            "Internal": False,
            "Removable": True,
            "DeviceIdentifier": "disk4",
            "VolumeUUID": "expected-volume",
        }
        volume = DjiMicVolume(Path("/Volumes/DJI"), "disk4", "DJI", "expected-volume")

        with patch("wav_merger.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            app.eject_worker(volume)

        self.assertEqual(run.call_args.args[0], ["diskutil", "eject", "disk4"])
        self.assertEqual(app.work_queue.get_nowait(), ("eject_done", "DJI"))

    def test_eject_worker_cancels_if_volume_identity_changes(self) -> None:
        app = WavMergerApp.__new__(WavMergerApp)
        app.work_queue = queue.Queue()
        app.disk_info = lambda _target: {
            "Internal": False,
            "Removable": True,
            "DeviceIdentifier": "disk4",
            "VolumeUUID": "replacement-volume",
        }
        volume = DjiMicVolume(Path("/Volumes/DJI"), "disk4", "DJI", "expected-volume")

        with patch("wav_merger.subprocess.run") as run:
            app.eject_worker(volume)

        run.assert_not_called()
        kind, message = app.work_queue.get_nowait()
        self.assertEqual(kind, "eject_error")
        self.assertIn("卷标识已发生变化", message)


if __name__ == "__main__":
    unittest.main()
