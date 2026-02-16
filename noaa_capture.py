#!/usr/bin/env python3
"""
NOAA Satellite Automatic Reception System
Predicts passes, captures signals, and processes images
"""

import os
import sys
import json
import subprocess
import time
import logging
import glob
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import math

try:
    import ephem
    import requests
except ImportError:
    print("Error: Required Python packages not installed.")
    print("Run: sudo pip3 install ephem requests --break-system-packages")
    sys.exit(1)


class NOAACapture:
    def __init__(self, config_path="~/noaa_reception/config.json"):
        self.config_path = os.path.expanduser(config_path)
        self.load_config()
        self.setup_logging()

    def load_config(self):
        """Load configuration from JSON file"""
        try:
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)

            # Expand paths
            for key in self.config['directories']:
                self.config['directories'][key] = os.path.expanduser(
                    self.config['directories'][key]
                )
        except FileNotFoundError:
            print(f"Error: Config file not found at {self.config_path}")
            print("Please create config.json with your location and settings.")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in config file: {e}")
            sys.exit(1)

    def setup_logging(self):
        """Setup logging to file and console"""
        log_dir = self.config['directories']['logs']
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, f"noaa_capture_{datetime.now().strftime('%Y%m%d')}.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def update_tle(self):
        """Download latest TLE data for NOAA satellites"""
        self.logger.info("Updating TLE data...")
        tle_dir = self.config['directories']['tle']
        os.makedirs(tle_dir, exist_ok=True)

        tle_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=noaa&FORMAT=tle"
        tle_file = os.path.join(tle_dir, "weather.tle")

        try:
            response = requests.get(tle_url, timeout=30)
            response.raise_for_status()

            with open(tle_file, 'w') as f:
                f.write(response.text)

            self.logger.info(f"TLE data updated successfully: {tle_file}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to update TLE data: {e}")
            return False

    def get_tle_data(self, sat_name):
        """Read TLE data for a specific satellite"""
        tle_file = os.path.join(self.config['directories']['tle'], "weather.tle")

        if not os.path.exists(tle_file):
            self.logger.error("TLE file not found. Run with --update-tle first.")
            return None

        try:
            with open(tle_file, 'r') as f:
                lines = f.readlines()

            # Search for satellite name
            for i in range(len(lines) - 2):
                if sat_name in lines[i]:
                    return {
                        'name': lines[i].strip(),
                        'line1': lines[i+1].strip(),
                        'line2': lines[i+2].strip()
                    }

            self.logger.error(f"Satellite {sat_name} not found in TLE file")
            return None
        except Exception as e:
            self.logger.error(f"Error reading TLE file: {e}")
            return None

    def calculate_pass_duration(self, sat_name, aos_time):
        """Calculate pass duration and max elevation"""
        tle = self.get_tle_data(sat_name)
        if not tle:
            return 0, 0

        try:
            # Setup observer
            observer = ephem.Observer()
            observer.lat = str(self.config['location']['latitude'])
            observer.lon = str(self.config['location']['longitude'])
            observer.elevation = self.config['location']['altitude']
            observer.date = aos_time

            # Setup satellite
            sat = ephem.readtle(tle['name'], tle['line1'], tle['line2'])

            # Find max elevation and LOS
            max_el = 0
            current_time = aos_time

            # Check elevation every 30 seconds for up to 20 minutes
            for _ in range(40):
                observer.date = current_time
                sat.compute(observer)

                el = math.degrees(sat.alt)
                if el > max_el:
                    max_el = el

                if el < 0 and current_time > aos_time:
                    # Satellite has set
                    duration = (current_time - aos_time) * 86400  # Convert to seconds
                    return int(duration), int(max_el)

                current_time = ephem.Date(current_time + ephem.second * 30)

            return 600, int(max_el)  # Default 10 minutes if we don't find LOS

        except Exception as e:
            self.logger.error(f"Error calculating pass duration: {e}")
            return 600, 0

    def predict_next_passes(self, hours=24):
        """Predict satellite passes for the next N hours"""
        self.logger.info(f"Predicting passes for next {hours} hours...")

        passes = []
        now = datetime.utcnow()
        end_time = now + timedelta(hours=hours)

        for sat_name, sat_config in self.config['satellites'].items():
            if not sat_config['enabled']:
                continue

            tle = self.get_tle_data(sat_name)
            if not tle:
                continue

            try:
                # Setup observer
                observer = ephem.Observer()
                observer.lat = str(self.config['location']['latitude'])
                observer.lon = str(self.config['location']['longitude'])
                observer.elevation = self.config['location']['altitude']
                observer.date = now

                # Setup satellite
                sat = ephem.readtle(tle['name'], tle['line1'], tle['line2'])

                # Find passes
                while observer.date.datetime() < end_time:
                    try:
                        tr, azr, tt, altt, ts, azs = observer.next_pass(sat)

                        if tr.datetime() > end_time:
                            break

                        max_alt_deg = math.degrees(altt)

                        if max_alt_deg >= self.config['reception']['min_elevation']:
                            duration, _ = self.calculate_pass_duration(sat_name, tr)

                            passes.append({
                                'satellite': sat_name,
                                'aos': tr.datetime(),
                                'los': ts.datetime(),
                                'max_elevation': max_alt_deg,
                                'duration': duration,
                                'frequency': sat_config['frequency']
                            })

                            self.logger.info(
                                f"{sat_name}: AOS {tr.datetime().strftime('%Y-%m-%d %H:%M:%S')} UTC, "
                                f"Max El: {max_alt_deg:.1f}°, Duration: {duration}s"
                            )

                        observer.date = ts + ephem.minute

                    except ValueError:
                        # No more passes
                        break

            except Exception as e:
                self.logger.error(f"Error predicting passes for {sat_name}: {e}")

        # Sort by AOS time
        passes.sort(key=lambda x: x['aos'])
        return passes

    def capture_pass(self, pass_info):
        """Capture a satellite pass"""
        sat_name = pass_info['satellite']
        aos = pass_info['aos']
        duration = pass_info['duration']
        frequency = pass_info['frequency']

        timestamp = aos.strftime('%Y%m%d_%H%M%S')
        filename_base = f"{sat_name.replace(' ', '_')}_{timestamp}"

        audio_dir = self.config['directories']['audio']
        os.makedirs(audio_dir, exist_ok=True)
        audio_file = os.path.join(audio_dir, f"{filename_base}.wav")

        self.logger.info(f"Starting capture: {sat_name} at {aos.strftime('%H:%M:%S')} UTC")

        # Wait until AOS
        now = datetime.utcnow()
        if aos > now:
            wait_seconds = (aos - now).total_seconds()
            self.logger.info(f"Waiting {wait_seconds:.0f} seconds until AOS...")
            time.sleep(wait_seconds)

        # RTL_FM command for NOAA APT reception
        gain = self.config['reception']['rtl_sdr_gain']
        sample_rate = self.config['reception']['sample_rate']
        freq_offset = self.config['reception']['frequency_offset']

        rtl_fm_cmd = [
            'rtl_fm',
            '-f', str(frequency + freq_offset),
            '-s', str(sample_rate),
            '-g', str(gain),
            '-p', '0',
            '-E', 'dc',
            '-F', '9',
            '-A', 'fast',
            audio_file
        ]

        self.logger.info(f"Recording for {duration} seconds...")
        self.logger.debug(f"Command: {' '.join(rtl_fm_cmd)}")

        try:
            # Start recording
            process = subprocess.Popen(rtl_fm_cmd,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

            # Let it run for the pass duration
            time.sleep(duration)

            # Stop recording
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

            self.logger.info(f"Capture complete: {audio_file}")

            # Process the audio into images
            if os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                self.process_audio(audio_file, filename_base, sat_name)
            else:
                self.logger.error(f"Audio file is empty or doesn't exist: {audio_file}")

        except Exception as e:
            self.logger.error(f"Error during capture: {e}")

    def send_to_epaper(self, image_file):
        """Send decoded image to e-Paper display node"""
        try:
            self.logger.info(f"Sending to e-Paper display: {image_file}")
            result = subprocess.run([
                'sshpass', '-p', '1234', 'scp', '-o', 'StrictHostKeyChecking=no',
                image_file, 'epaper@192.168.1.220:~/incoming/'
            ], timeout=30, capture_output=True, text=True)
            if result.returncode == 0:
                self.logger.info("Image sent to e-Paper display successfully")
            else:
                self.logger.error(f"Failed to send to display: {result.stderr}")
        except Exception as e:
            self.logger.error(f"Failed to send to display: {e}")

    def process_audio(self, audio_file, filename_base, sat_name):
        """Process captured audio into images"""
        self.logger.info(f"Processing audio: {audio_file}")

        image_dir = self.config['directories']['images']
        os.makedirs(image_dir, exist_ok=True)

        # Resample audio to 11025 Hz (required for noaa-apt)
        resampled_file = audio_file.replace('.wav', '_resampled.wav')

        try:
            sox_cmd = [
                'sox', '-t', 'raw', '-r', '60000', '-e', 'signed', '-b', '16', '-c', '1',
                audio_file,
                '-r', '11025',
                resampled_file
            ]
            subprocess.run(sox_cmd, check=True, capture_output=True)
            self.logger.info("Audio resampled to 11025 Hz")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to resample audio: {e}")
            return

        # Generate images using noaa-apt
        config = self.config['processing']
        first_image = None

        # Basic image
        if config.get('generate_basic', True):
            output_file = os.path.join(image_dir, f"{filename_base}.png")
            try:
                noaa_apt_cmd = [
                    'noaa-apt',
                    resampled_file,
                    '-o', output_file
                ]
                subprocess.run(noaa_apt_cmd, check=True, capture_output=True)
                self.logger.info(f"Generated basic image: {output_file}")
                if first_image is None:
                    first_image = output_file
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to generate basic image: {e.stderr.decode()}")

        # False color images (MSA, MCIR, etc.)
        if config.get('generate_msa', True):
            output_file = os.path.join(image_dir, f"{filename_base}_MSA.png")
            try:
                noaa_apt_cmd = [
                    'noaa-apt',
                    resampled_file,
                    '-c', 'msa',
                    '-o', output_file
                ]
                subprocess.run(noaa_apt_cmd, check=True, capture_output=True)
                self.logger.info(f"Generated MSA image: {output_file}")
            except subprocess.CalledProcessError as e:
                self.logger.debug(f"MSA image generation skipped or failed: {e.stderr.decode()}")

        if config.get('generate_msa_precip', True):
            output_file = os.path.join(image_dir, f"{filename_base}_MSA_PRECIP.png")
            try:
                noaa_apt_cmd = [
                    'noaa-apt',
                    resampled_file,
                    '-c', 'msa-precip',
                    '-o', output_file
                ]
                subprocess.run(noaa_apt_cmd, check=True, capture_output=True)
                self.logger.info(f"Generated MSA-PRECIP image: {output_file}")
            except subprocess.CalledProcessError as e:
                self.logger.debug(f"MSA-PRECIP image generation skipped or failed")

        if config.get('generate_hvct', True):
            output_file = os.path.join(image_dir, f"{filename_base}_HVCT.png")
            try:
                noaa_apt_cmd = [
                    'noaa-apt',
                    resampled_file,
                    '-c', 'hvct',
                    '-o', output_file
                ]
                subprocess.run(noaa_apt_cmd, check=True, capture_output=True)
                self.logger.info(f"Generated HVCT image: {output_file}")
            except subprocess.CalledProcessError as e:
                self.logger.debug(f"HVCT image generation skipped or failed")

        if config.get('generate_therm', True):
            output_file = os.path.join(image_dir, f"{filename_base}_THERM.png")
            try:
                noaa_apt_cmd = [
                    'noaa-apt',
                    resampled_file,
                    '-c', 'therm',
                    '-o', output_file
                ]
                subprocess.run(noaa_apt_cmd, check=True, capture_output=True)
                self.logger.info(f"Generated THERM image: {output_file}")
            except subprocess.CalledProcessError as e:
                self.logger.debug(f"THERM image generation skipped or failed")

        # Send the first successfully generated image to e-Paper display
        if first_image and os.path.exists(first_image):
            self.send_to_epaper(first_image)
        else:
            # Try to find any generated image
            all_images = glob.glob(os.path.join(image_dir, f"{filename_base}*.png"))
            if all_images:
                self.send_to_epaper(sorted(all_images)[0])

        # Clean up resampled file
        if not config.get('save_raw_audio', True):
            try:
                os.remove(audio_file)
                self.logger.info(f"Removed raw audio file: {audio_file}")
            except:
                pass

        try:
            os.remove(resampled_file)
        except:
            pass

    def schedule_next_pass(self):
        """Find and schedule the next satellite pass"""
        passes = self.predict_next_passes(hours=24)

        if not passes:
            self.logger.warning("No passes found in the next 24 hours")
            return None

        next_pass = passes[0]
        self.logger.info(
            f"Next pass: {next_pass['satellite']} at "
            f"{next_pass['aos'].strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"(Max elevation: {next_pass['max_elevation']:.1f}°)"
        )

        return next_pass

    def run_scheduler(self):
        """Continuous scheduler that captures all passes"""
        self.logger.info("Starting NOAA satellite scheduler...")

        while True:
            try:
                next_pass = self.schedule_next_pass()

                if next_pass:
                    # Wait until 2 minutes before AOS
                    now = datetime.utcnow()
                    wait_until = next_pass['aos'] - timedelta(minutes=2)

                    if wait_until > now:
                        wait_seconds = (wait_until - now).total_seconds()
                        self.logger.info(f"Sleeping for {wait_seconds/3600:.1f} hours...")
                        time.sleep(wait_seconds)

                    # Capture the pass
                    self.capture_pass(next_pass)
                else:
                    # No passes, check again in 6 hours
                    self.logger.info("No passes scheduled, checking again in 6 hours...")
                    time.sleep(6 * 3600)

            except KeyboardInterrupt:
                self.logger.info("Scheduler stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in scheduler: {e}")
                time.sleep(300)  # Wait 5 minutes before retry


def main():
    parser = argparse.ArgumentParser(
        description='NOAA Satellite Automatic Reception System'
    )
    parser.add_argument(
        '--config',
        default='~/noaa_reception/config.json',
        help='Path to configuration file'
    )
    parser.add_argument(
        '--update-tle',
        action='store_true',
        help='Update TLE data and exit'
    )
    parser.add_argument(
        '--predict',
        type=int,
        metavar='HOURS',
        help='Predict passes for next N hours and exit'
    )
    parser.add_argument(
        '--schedule',
        action='store_true',
        help='Run scheduler to automatically capture all passes'
    )

    args = parser.parse_args()

    # Create capture object
    capture = NOAACapture(config_path=args.config)

    # Handle commands
    if args.update_tle:
        capture.update_tle()
        return

    if args.predict:
        passes = capture.predict_next_passes(hours=args.predict)
        if passes:
            print(f"\nFound {len(passes)} passes in the next {args.predict} hours:\n")
            for p in passes:
                print(f"{p['satellite']:8s} | "
                      f"{p['aos'].strftime('%Y-%m-%d %H:%M')} UTC | "
                      f"Max El: {p['max_elevation']:5.1f}° | "
                      f"Duration: {p['duration']:3d}s")
        else:
            print(f"No passes found in the next {args.predict} hours")
        return

    if args.schedule:
        capture.run_scheduler()
        return

    # Default: show usage
    parser.print_help()


if __name__ == '__main__':
    main()
