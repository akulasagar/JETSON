"""Camera capture thread using OpenCV.

Handles USB camera connection, reconnection, frame capture with
crosshair overlay, and simulation frames when no camera is available.
"""
import time
import logging

import cv2
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

logger = logging.getLogger(__name__)


class CameraThread(QThread):
    """Captures frames from a USB camera and emits QImage signals."""

    frame_available = pyqtSignal(QImage, int)
    camera_error = pyqtSignal(str, int)
    camera_reconnecting = pyqtSignal(str, int)

    def __init__(self, camera_index=0, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self.running = True
        self.last_frame = None
        self.cap = None
        self.target_width = 1280
        self.target_height = 720

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self):
        self._setup_usb_camera()

    # ------------------------------------------------------------------
    # Camera connection logic
    # ------------------------------------------------------------------
    def _setup_usb_camera(self):

        # Port groups for each camera
        if self.camera_index == 0:
            candidate_ports = [0, 1]  # /dev/video0 or /dev/video1
        else:
            candidate_ports = [2, 3]  # /dev/video2 or /dev/video3

        backends = [cv2.CAP_V4L2, cv2.CAP_ANY]

        while self.running:

            if not self.cap or not self.cap.isOpened():

                opened = False

                for device_index in candidate_ports:

                    logger.info(f"[CAM-{self.camera_index}] Trying /dev/video{device_index}")

                    self.camera_reconnecting.emit(
                        f"Connecting camera {self.camera_index + 1}…",
                        self.camera_index
                    )

                    for backend in backends:

                        cap = cv2.VideoCapture(device_index, backend)

                        if cap.isOpened():

                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
                            cap.set(cv2.CAP_PROP_FPS, 30)

                            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            fps = cap.get(cv2.CAP_PROP_FPS)

                            logger.info(
                                f"[CAM-{self.camera_index}] Connected on /dev/video{device_index} "
                                f"{w}x{h} @ {fps:.1f} fps"
                            )

                            self.cap = cap
                            opened = True

                            self.camera_reconnecting.emit(
                                f"Camera {self.camera_index + 1} connected",
                                self.camera_index
                            )

                            break

                        else:
                            cap.release()

                    if opened:
                        break

                if not opened:

                    logger.error(
                        f"[CAM-{self.camera_index}] Camera not found in ports {candidate_ports}"
                    )

                    self.camera_error.emit(
                        f"Camera {self.camera_index + 1} unavailable",
                        self.camera_index
                    )

                    self._emit_simulation_frame()

                    time.sleep(5)
                    continue

            self._read_frames(device_index)

    # ------------------------------------------------------------------
    # Frame read loop
    # ------------------------------------------------------------------
    def _read_frames(self, device_index):

        while self.running and self.cap and self.cap.isOpened():

            ret, frame = self.cap.read()

            if ret and frame is not None:

                self.last_frame = frame.copy()

                frame_resized = cv2.resize(
                    frame, (self.target_width, self.target_height)
                )

                cx = self.target_width // 2
                cy = self.target_height // 2

                gap = 15
                line_len = 18
                color = (240, 240, 240)
                thickness = 2

                cv2.line(frame_resized, (cx - gap - line_len, cy), (cx - gap, cy),
                         color, thickness, cv2.LINE_AA)
                cv2.line(frame_resized, (cx + gap, cy), (cx + gap + line_len, cy),
                         color, thickness, cv2.LINE_AA)
                cv2.line(frame_resized, (cx, cy - gap - line_len), (cx, cy - gap),
                         color, thickness, cv2.LINE_AA)
                cv2.line(frame_resized, (cx, cy + gap), (cx, cy + gap + line_len),
                         color, thickness, cv2.LINE_AA)

                frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)

                h, w, ch = frame_rgb.shape

                q_img = QImage(
                    frame_rgb.data,
                    w,
                    h,
                    ch * w,
                    QImage.Format_RGB888
                )

                self.frame_available.emit(q_img.copy(), self.camera_index)

            else:

                logger.warning(
                    f"[CAM-{self.camera_index}] Frame read failed – reconnecting…"
                )

                if self.cap:
                    self.cap.release()
                    self.cap = None

                self.camera_reconnecting.emit(
                    f"Camera {self.camera_index + 1} disconnected",
                    self.camera_index
                )

                break

            time.sleep(0.033)

        if self.cap and self.cap.isOpened():
            self.cap.release()
            self.cap = None

    # ------------------------------------------------------------------
    # Simulation frame
    # ------------------------------------------------------------------
    def _emit_simulation_frame(self):

        frame = np.zeros((self.target_height, self.target_width, 3), dtype=np.uint8)

        color = (40, 40, 50) if self.camera_index == 0 else (30, 30, 45)

        frame[:] = color

        cv2.putText(
            frame,
            f"Camera {self.camera_index + 1} – No Signal",
            (350, self.target_height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (90, 90, 100),
            2
        )

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        h, w, ch = frame_rgb.shape

        q_img = QImage(
            frame_rgb.data,
            w,
            h,
            ch * w,
            QImage.Format_RGB888
        )

        self.frame_available.emit(q_img.copy(), self.camera_index)

    # ------------------------------------------------------------------
    def get_last_frame(self):
        return self.last_frame

    # ------------------------------------------------------------------
    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        self.wait()
