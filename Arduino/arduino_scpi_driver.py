"""
Arduino SCPI driver — wraps serial communication to ArduinoSCPI firmware.

Usage:
    from arduino_scpi_driver import ArduinoSCPI

    dev = ArduinoSCPI("COM3")          # or "/dev/ttyUSB0"
    print(dev.idn())
    dev.digital_dir(13, "OUT")
    dev.digital_write(13, 1)
    val = dev.analog_read(0)           # reads A0
    dev.close()
"""

import serial
import time


class ArduinoSCPIError(Exception):
    pass


class ArduinoSCPI:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        self._ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        # Arduino resets on serial open; wait for READY
        time.sleep(2.0)
        self._flush()

    # ------------------------------------------------------------------
    # Low-level transport
    # ------------------------------------------------------------------

    def _flush(self):
        self._ser.reset_input_buffer()

    def _send(self, cmd: str) -> str:
        self._ser.write((cmd + "\n").encode())
        line = self._ser.readline().decode().strip()
        if line.startswith("ERR:"):
            raise ArduinoSCPIError(line[4:])
        return line

    def close(self):
        self._ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # IEEE 488.2 common commands
    # ------------------------------------------------------------------

    def idn(self) -> str:
        """*IDN? — identification string."""
        return self._send("*IDN?")

    def rst(self):
        """*RST — reset all pins."""
        self._send("*RST")

    # ------------------------------------------------------------------
    # Digital pin commands
    # ------------------------------------------------------------------

    def digital_dir(self, pin: int, direction: str):
        """Set digital pin direction: 'IN' or 'OUT'."""
        direction = direction.upper()
        if direction not in ("IN", "OUT"):
            raise ValueError("direction must be 'IN' or 'OUT'")
        self._send(f"DIG:DIR {pin},{direction}")

    def digital_dir_query(self, pin: int) -> str:
        """Query digital pin direction: returns 'IN' or 'OUT'."""
        return self._send(f"DIG:DIR? {pin}")

    def digital_write(self, pin: int, value: int):
        """Write 0 or 1 to a digital pin (auto-sets to OUTPUT)."""
        self._send(f"DIG:WRITE {pin},{1 if value else 0}")

    def digital_read(self, pin: int) -> int:
        """Read a digital pin; returns 0 or 1."""
        return int(self._send(f"DIG:READ? {pin}"))

    # ------------------------------------------------------------------
    # Analog pin commands
    # ------------------------------------------------------------------

    def analog_read(self, channel: int) -> int:
        """Read analog channel 0-5 (A0-A5); returns 0-1023."""
        return int(self._send(f"ANA:READ? {channel}"))

    def analog_write(self, pin: int, value: int):
        """Write PWM value 0-255 to a pin (auto-sets to OUTPUT)."""
        self._send(f"ANA:WRITE {pin},{value}")

    # ------------------------------------------------------------------
    # Port (byte-wide) commands — pins 0-7
    # ------------------------------------------------------------------

    def port_dir(self, mask: int):
        """Set direction bitmask for pins 0-7 (1=OUT, 0=IN)."""
        self._send(f"PORT:DIR {mask & 0xFF}")

    def port_dir_query(self) -> int:
        """Query direction bitmask for pins 0-7."""
        return int(self._send("PORT:DIR?"))

    def port_write(self, mask: int):
        """Write bitmask to pins 0-7 (auto-sets all to OUTPUT)."""
        self._send(f"PORT:WRITE {mask & 0xFF}")

    def port_read(self) -> int:
        """Read bitmask from pins 0-7."""
        return int(self._send("PORT:READ?"))
