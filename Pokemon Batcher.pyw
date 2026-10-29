"""Double-click to start the app without a console window."""
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
runpy.run_path(os.path.join(here, 'app.py'), run_name='__main__')
