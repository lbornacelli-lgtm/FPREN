import pyloudnorm as pyln
import soundfile as sf
import numpy as np

TARGET_LUFS = -16.0

def normalize_audio(path):
    data, rate = sf.read(path)
    meter = pyln.Meter(rate)
    loudness = meter.integrated_loudness(data)

    normalized_audio = pyln.normalize.loudness(data, loudness, TARGET_LUFS)
    sf.write(path, normalized_audio, rate)
