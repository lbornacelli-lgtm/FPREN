from pedalboard import Pedalboard, Compressor, Limiter, Gain
from pedalboard.effects import HighShelfFilter, LowShelfFilter, Reverb, Delay
import soundfile as sf

def apply_audio_chain(input_file: str, output_file: str):
    """
    Applies a professional audio chain to a WAV file.
    Compatible with Pedalboard 0.9.22
    """
    # Read audio
    audio, samplerate = sf.read(input_file)

    # Create pedalboard chain
    board = Pedalboard([
        Gain(3.0),                                # Boost overall level
        Compressor(threshold_db=-20, ratio=3.0), # Dynamic range control
        Limiter(threshold_db=-1.0),              # Prevent clipping
        HighShelfFilter(cutoff_hz=10000, gain_db=4.0), # Add air
        LowShelfFilter(cutoff_hz=120, gain_db=3.0),    # Add warmth
        Reverb(room_size=0.3, wet_level=0.2),    # Subtle reverb
        Delay(delay_time_s=0.25, feedback=0.2, wet_level=0.15)  # Slight echo
    ])

    # Process audio
    processed_audio = board(audio, samplerate)

    # Write output
    sf.write(output_file, processed_audio, samplerate)
