import sys
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

app = QApplication(sys.argv)

# Main window
window = QWidget()
window.setWindowTitle("PyQt5 Test Window")
window.setGeometry(400, 200, 600, 300)  # x, y, width, height

# Label
label = QLabel("✅ PyQt5 is Working")
label.setAlignment(Qt.AlignCenter)

# Bigger font
font = QFont("Arial", 24)
label.setFont(font)

# Layout
layout = QVBoxLayout()
layout.addWidget(label)

window.setLayout(layout)

# Show window
window.show()

sys.exit(app.exec())


