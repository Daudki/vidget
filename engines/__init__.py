from .video import VideoEngine
from .direct import DirectEngine

DEFAULT_ENGINE = 'video'

ENGINES = {engine.key: engine for engine in (VideoEngine(), DirectEngine())}
