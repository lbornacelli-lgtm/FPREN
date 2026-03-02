# ~/weather_station/processing/audio_chain.py

from pedalboard import Pedalboard, Compressor, Limiter, Gain, HighShelfFilter, LowShelfFilter, Reverb, Delay
import soundfile as sf

def apply_audio_chain(input_file: str, output_file: str):
    """
    Apply a professional audio chain to a WAV file.
    Compatible with Python 3.12 + Pedalboard 0.9.22
    """
    # Read audio
    audio, samplerate = sf.read(input_file)

    # Pedalboard chain
    board = Pedalboard([
        Gain(3.0),
        Compressor(threshold_db=-20, ratio=3.0),
        Limiter(threshold_db=-1.0),
        HighShelfFilter(cutoff_hz=10000, gain_db=4.0),
        LowShelfFilter(cutoff_hz=120, gain_db=3.0),
        Reverb(room_size=0.3, wet_level=0.2),
        Delay(delay_time_s=0.25, feedback=0.2, wet_level=0.15)
    ])

    # Process audio
    processed_audio = board(audio, samplerate)

    # Save output
    sf.write(output_file, processed_audio, samplerate)
