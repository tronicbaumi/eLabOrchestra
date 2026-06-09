"""Quick demo of the ArduinoSCPI driver."""

from arduino_scpi_driver import ArduinoSCPI

PORT = "COM3"   # adjust to your port

with ArduinoSCPI(PORT) as dev:
    print("IDN:", dev.idn())

    # Blink LED on pin 13
    dev.digital_dir(13, "OUT")
    for _ in range(3):
        dev.digital_write(13, 1)
        import time; time.sleep(0.5)
        dev.digital_write(13, 0)
        time.sleep(0.5)

    # Read analog A0
    val = dev.analog_read(0)
    print(f"A0 = {val}  ({val / 1023 * 5:.3f} V)")

    # PWM on pin 9
    dev.analog_write(9, 128)   # ~50 % duty cycle
    time.sleep(1)
    dev.analog_write(9, 0)

    # Port-wide byte write to pins 0-7
    dev.port_dir(0xFF)          # all outputs
    dev.port_write(0b10101010)
    time.sleep(0.5)
    dev.port_write(0b01010101)
    time.sleep(0.5)
    dev.port_write(0)

    dev.rst()
    print("Done.")
