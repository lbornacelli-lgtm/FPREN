import runpy, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "weather_rss", "web"))
runpy.run_path(os.path.join(os.path.dirname(__file__), "weather_rss", "web", "app.py"),
               run_name="__main__")
