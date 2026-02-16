[README-noaa.md](https://github.com/user-attachments/files/25351667/README-noaa.md)
# 🛰️ NOAA Satellite Receiver

Automated NOAA APT weather satellite image capture and decode system using an RTL-SDR Blog V4 on a Raspberry Pi Zero 2 W.

Receives live weather images directly from NOAA 15, NOAA 18, and NOAA 19 satellites as they pass overhead, decodes them using `noaa-apt`, and pushes the images to a Waveshare e-Paper display.

## Hardware

| Component | Model |
|-----------|-------|
| Computer | Raspberry Pi Zero 2 W |
| SDR | RTL-SDR Blog V4 (R828D tuner) |
| Antenna | V-Dipole, 53cm elements at 120°, horizontal |
| Display | Waveshare 7.3" e-Paper HAT (E) on separate Pi |

## How It Works

1. `noaa_capture.py` predicts satellite passes using TLE orbital data and the `ephem` library
2. Sleeps until 2 minutes before the next pass
3. Captures the 137 MHz APT signal using `rtl_fm`
4. Resamples the raw audio to 11025 Hz with `sox`
5. Decodes the image using `noaa-apt`
6. Auto-transfers the decoded image to the e-Paper display node via SCP

## Configuration

Edit `config.json` with your location and preferences:

```json
{
  "location": {
    "latitude": 29.4953,
    "longitude": -95.1547,
    "altitude": 15.0,
    "name": "League City, TX"
  },
  "satellites": {
    "NOAA 15": {"frequency": 137620000, "enabled": true},
    "NOAA 18": {"frequency": 137912500, "enabled": true},
    "NOAA 19": {"frequency": 137100000, "enabled": true}
  },
  "reception": {
    "min_elevation": 25,
    "rtl_sdr_gain": 33.8,
    "sample_rate": 60000
  }
}
```

## Installation

### 1. Install RTL-SDR Blog V4 Drivers (CRITICAL)

The V4 requires custom drivers — the stock `rtl-sdr` package will NOT work (causes PLL lock errors):

```bash
sudo apt purge -y librtlsdr-dev librtlsdr0 rtl-sdr
sudo apt install -y git build-essential cmake libusb-1.0-0-dev
git clone https://github.com/rtlsdrblog/rtl-sdr-blog.git
cd rtl-sdr-blog && mkdir build && cd build
cmake .. && make && sudo make install && sudo ldconfig
```

Verify with `rtl_test -t` — you should see "RTL-SDR Blog V4 Detected" with NO "PLL not locked" messages.

### 2. Blacklist DVB-T Drivers

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
```

### 3. Install Dependencies

```bash
sudo apt install -y sox libsox-fmt-all sshpass python3-pip
pip install ephem requests --break-system-packages
```

### 4. Install noaa-apt

```bash
wget https://github.com/martinber/noaa-apt/releases/download/v1.4.1/noaa-apt-1.4.1-armv7-linux-gnueabihf-nogui.zip
unzip noaa-apt-*.zip
sudo cp noaa-apt /usr/local/bin/
sudo chmod +x /usr/local/bin/noaa-apt
sudo mkdir -p /usr/local/share/noaa-apt
sudo cp -r res /usr/local/share/noaa-apt/
```

### 5. Update TLE Data

```bash
cd ~/noaa_reception
python3 noaa_capture.py --update-tle
```

## Usage

### Predict upcoming passes
```bash
python3 noaa_capture.py --predict 24
```

### Run the automated scheduler
```bash
nohup python3 noaa_capture.py --schedule > ~/noaa_capture.log 2>&1 &
```

### Monitor the log
```bash
tail -f ~/noaa_capture.log
```

## Antenna Tips

- The V-dipole elements should each be **53cm (21 inches)** for 137 MHz
- Mount **horizontally** (parallel to the ground) — NOAA satellites are overhead
- Spread elements at **120° angle**
- Place **outdoors** with clear sky view — even ground level beats indoors
- A QFH (Quadrifilar Helix) antenna is the gold standard upgrade (~$30-50)

## Part of the Neighborhood Intel Station

This is one module of the [Neighborhood Intel Station](https://danamald.github.io/neighborhood-intel-station/) — a multi-source RF intelligence dashboard combining NOAA satellite imagery, Ambient Weather data, Meshtastic mesh networking, and RF spectrum monitoring.

## License

MIT
