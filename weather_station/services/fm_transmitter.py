import logging
import subprocess
import os

class FMTransmitter:
    def __init__(self, settings):
        self.logger = logging.getLogger("FMTransmitter")
        self.settings = settings
        self.logger.info("FMTransmitter initialized")

    def play_wav(self, wav_file):
        """
        Send WAV file to FM transmitter hardware.
        Replace 'fm_transmit_command' with actual system call for your device.
        """
        if not os.path.isfile(wav_file):
            self.logger.warning(f"File does not exist: {wav_file}")
            return

        try:
            # Example command — replace with your actual transmitter command
            cmd = ["fm_transmit_command", wav_file]  
            subprocess.run(cmd, check=True)
            self.logger.info(f"Broadcasting over FM: {wav_file}")
        except Exception as e:
            self.logger.error(f"FM transmit error: {e}")
