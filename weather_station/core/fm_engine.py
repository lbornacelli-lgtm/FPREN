import os
import subprocess
from config.fm_transmitter import TRANSMITTER_DEVICE

def send_to_transmitter(file_path):
    # Replace 'sox' with your transmitter tool if different
    subprocess.run([
        "sox", file_path,
        "-r", "44100", "-c", "2",
        "-V1", TRANSMITTER_DEVICE
    ])
