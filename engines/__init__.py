from .video import VideoEngine
from .direct import DirectEngine
from .photos import PhotosEngine

DEFAULT_ENGINE = 'video'

ENGINES = {engine.key: engine for engine in (VideoEngine(), DirectEngine(), PhotosEngine())}
