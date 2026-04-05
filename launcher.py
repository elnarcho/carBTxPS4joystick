"""Launcher that ensures DLLs are found before importing the app."""
import os
import sys

# When running as frozen exe, add the temp extraction dir to DLL search path
if getattr(sys, 'frozen', False):
    base = sys._MEIPASS
    os.add_dll_directory(base)
    os.environ['PATH'] = base + os.pathsep + os.environ.get('PATH', '')
    os.environ['TCL_LIBRARY'] = os.path.join(base, 'tcl', 'tcl8.6')
    os.environ['TK_LIBRARY'] = os.path.join(base, 'tcl', 'tk8.6')

# Now import and run the app
from qcar_controller import QCARApp
import pygame

if __name__ == "__main__":
    app = QCARApp()
    app.mainloop()
    pygame.quit()
