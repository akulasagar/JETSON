# 🏗️ Project Shudh: Mission Control for Sewage Robotics

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-Computer_Vision-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white)
![Azure](https://img.shields.io/badge/Azure-Cloud_Storage-0078D4?style=for-the-badge&logo=microsoft-azure&logoColor=white)

## 🚀 Project Overview

**Project Shudh** is a specialized mission-control dashboard for an autonomous/teleoperated sewage cleaning robot. It solves the critical challenge of **real-time environmental monitoring and data logging** in hazardous underground environments. By integrating multi-sensor telemetry (Gas, Depth, GPS) with high-fidelity vision systems, it provides operators with a comprehensive, safe, and data-driven workspace.

---

## 🏛️ Architecture (System Design)

The project follows a **Decoupled Asynchronous Architecture**, essential for maintaining a high-performance UI while handling slow/unreliable hardware I/O.

### The 'Frontend vs Backend vs Service' Model

| Layer | Component | Responsibility |
| :--- | :--- | :--- |
| **Frontend (View)** | **PyQt5 GUI** | Handles user interaction, camera rendering, and state visualization. |
| **Backend (Core)** | **Hardware Threads** | Asynchronous workers managing Serial, Modbus, and OpenCV streams. |
| **Services** | **Cloud & Voice** | Azure Blob Storage synchronization and Multi-lingual Voice feedback. |

### High-Level Data Flow
1.  **Ingestion**: Background threads (Service workers) poll sensors (Gas, GPS, Modbus) and capture video frames.
2.  **Transmission**: Data is pushed via **PyQt Signals** to the `MainDashboard` (The Orchestrator).
3.  **Visualization**: The UI thread updates gauges, maps, and video feeds at 30-60 FPS without blocking.
4.  **Persistence**: On operation finish, data is bundled into JSON/CSV and synced to **Azure Cloud** and local **PostgreSQL**.

---

## 📦 Module-by-Module Breakdown

### 📂 `gui/` - The Interface Layer
Contains the visualization logic. Optimized for high-contrast viewing in field conditions.
*   **`dashboard.py`**: The central controller. Orchestrates lifecycle for all background threads.
*   **`screens/`**: Contains `MainScreen` (Inspection) and `PipeCleaning` (Active telemetry) widgets.
*   **`widgets/`**: Reusable custom UI elements like gas gauges and progress bars.

### 📂 `hardware/` - Sensor Integration (The 'Backend')
Each file here is a `QThread` subclass, ensuring that a slow sensor reading (e.g., a GPS timeout) never freezes the UI.
*   **`camera_thread.py`**: Multi-instance OpenCV capture for dual-feed support.
*   **`gas_thread.py`**: Monitors H2S, CO, and CH4 levels for operator safety.
*   **`modbus_thread.py`**: Handles industrial-grade hardware comms (PLCs, motors).

### 📂 `core/` - Business Logic & Services
*   **`data_uploader.py`**: Manages the upload queue for Azure Blob Storage.
*   **`voice_module.py`**: Provides bilingual (English/Telugu) voice feedback using a cached TTS engine.

---

## 💻 Critical Code Snippets

### 1. The Thread Orchestrator (`main.py`)
Ensures proper initialization order, especially for `QWebEngineView`, which is notoriously sensitive to C-level startup order.

```python
# CRITICAL: QWebEngineView must be imported BEFORE QApplication is created.
# Importing after QApplication causes SIGABRT / core dump.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
from PyQt5.QtWebEngineWidgets import QWebEngineView

from PyQt5.QtWidgets import QApplication
from gui.dashboard import MainDashboard

def main():
    app = QApplication(sys.argv)
    window = MainDashboard() # Hardware threads start inside here
    window.show()
    sys.exit(app.exec_())
```

### 2. High-Performance Signal Routing (`dashboard.py`)
This demonstrates how hardware data (Producer) is safely routed to UI elements (Consumer) across thread boundaries.

```python
# In dashboard.py __init__
self.gas_thread = GasThread()
# Direct signal-to-slot routing
self.gas_thread.data_received.connect(self.pipe_screen.update_gas_data)
self.gas_thread.data_received.connect(self._handle_gas_update)
self.gas_thread.start()
```

---

## 🛠️ Tech Stack & Rationale

*   **PyQt5**: Chosen for its robust C++ backed performance. Essential for high-resolution video rendering and complex multi-threaded state management.
*   **OpenCV**: The industry standard for computer vision. Used here for frame acquisition, scaling, and eventual AI-based defect detection.
*   **PySerial/Modbus**: Reliable, low-level communication protocols for hardware-to-PC interfaces.
*   **Azure Identity/Blob**: Enterprise-grade cloud synchronization ensuring data durability and remote stakeholder access.

---

## ⚙️ Installation & Usage

### 0. Prerequisites
- **OS**: Ubuntu 20.04+ (Recommended)
- **Cameras**: At least 2 USB UVC compatible cameras.
- **Sensors**: GPS (ttyUSB1), Modbus (ttyCH341USB0).

### 1. Environment Setup
```bash
# Clone the repository
git clone https://github.com/your-repo/project-shudh.git
cd project-shudh

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Execution
```bash
# Ensure UI permissions for Raspberry Pi/Jetson (if applicable)
export DISPLAY=:0
python3 main.py
```

---

## ✨ Feature Highlights

*   🛡️ **Hazardous Environment Monitoring**: Real-time gas sensing with visual and auditory alarms.
*   👁️ **Dual-Streaming Vision**: Simultaneous 60FPS feed from two cameras with zero-latency switching.
*   🎙️ **Bilingual Voice Engine**: Concurrent English and Telugu status announcements for local accessibility.
*   ☁️ **Automatic Cloud Sync**: Background synchronization of inspection data to Azure Blob Storage with offline retry logic.
*   🛰️ **Precision Telemetry**: Integrated GPS tracking and Depth sensing for exact location/debris reporting.

---
> [!IMPORTANT]
> This application requires root access to serial ports. Ensure your user is in the `dialout` group: `sudo usermod -a -G dialout $USER`.
