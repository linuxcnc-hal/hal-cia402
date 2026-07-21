# hal-cia402
HAL Interface for CiA402 Devices,

## Graphical HAL wiring editor

This repository includes an initial graphical editor for building the HAL
connections between LinuxCNC joints, `cia402` instances, and the PDO pins
declared in `ethercat-conf.xml`.

The editor provides:

* Simulink-style blocks, ports, and drag-to-connect wires.
* Automatic parsing of `cia402.comp` pins and parameters.
* Automatic parsing of EtherCAT `pdoEntry` pins, types, and directions.
* LinuxCNC joint blocks based on the `motion(9)` joint interface.
* HAL type, direction, duplicate writer, and duplicate signal validation.
* Editable `cia402` parameters by double-clicking a component block.
* Project save/load using JSON and deterministic `.hal` file export.

Install the Qt dependency on a LinuxCNC Debian installation:

```bash
sudo apt install python3-pyqt5
```

Start the editor while LinuxCNC is running:

```bash
python3 -m cia402_gui
```

The editor automatically discovers the loaded `joint.N.*` and `cia402.N.*`
pins and current `cia402` parameter values. Use **File > Import EtherCAT XML**
to select `ethercat-conf.xml`; no joint or component counts are required.
Discovery uses the LinuxCNC Python HAL API and falls back to `halcmd` on older
installations. Press **F5** to refresh the live HAL blocks.

An XML file can still be opened directly from the command line:

```bash
python3 -m cia402_gui \
    --xml /path/to/ethercat-conf.xml \
    --comp /path/to/cia402.comp \
    --joints 3 \
    --instances 3
```

Drag from one port to another and enter a HAL signal name. Double-click a
`cia402` block to edit its parameters. Use **File > Preview HAL** to inspect
the output and **File > Export HAL** to write `cia402-generated.hal`.

The generated file contains component parameters and signal connections. Keep
it separate from hand-written setup and thread-order configuration, then load
it from the main HAL configuration with:

```hal
source cia402-generated.hal
```

Run the parser, validation, and generator tests without Qt:

```bash
python3 -m unittest discover -s tests -v
```

this component acts as a glue layer between hardware to Hal modules like Ethercat, CAN-Bus or others.

It translates raw IO Data from the PDOs to the common linuxcnc Hal pin structure and has build in logic
for the CiA402 State Control, feedback handling, external homing and build in scaling functions.

It delivers two functions: read_all and write_all.


The concept of integration in the correspondending task should be as following: 



  HARDWARE INPUT-->--CiA402_read-->--Motion-->--CiA402_write-->--Hardware Output

Hal Example:

  #Setup

    loadrt [KINS]KINEMATICS

    loadrt [EMCMOT]EMCMOT servo_period_nsec=[EMCMOT]SERVO_PERIOD num_joints=[KINS]JOINTS

    (loadusr -W lcec_conf ethercat-conf.xml)

    loadrt lcec

    loadrt cia402 count=3

    loadrt pid names=x-pid,y-pid,z-pid



  #Functions servo-thread

    addf lcec.read-all servo-thread

    addf cia402.0.read-all servo-thread

    addf cia402.1.read-all servo-thread

    addf cia402.2.read-all servo-thread

    addf motion/ PIDs / PCL / etc .

    addf cia402.0.write-all servo-thread

    addf cia402.1.write-all servo-thread

    addf cia402.2.write-all servo-thread

    addf lcec.write-all servo-thread

  
  #nets .....



Modes of Operation: 

  By default the component is set to CSP Mode, CSV Mode could be selected with an: 
  
    setp cia402.0.csp-mode 0  in hal.

  Mode changing in runtime is currently not supported, to avoid
  unwanted behaviour.

Homing:

  For using the servo drives internal homing procedure configure your
  joint homing to  Home on Index Pulse only and connect the components
  home input to the motion index-enable Pin:

    HOME_SEARCH_VEL = 0.0
    HOME_LATCH_VEL = 0.2  (Any value but zero, the homing speed is predetermined by the drives configured speed
    HOME_USE_INDEX = TRUE

  If you are using PIDs, don't forget to connect the PIDs index-enable pin.


Flexibility:

  Even though this component exports many pins, you can choose which functions you want to use:

  If you would like to use the CiA State Machine, connect Statusword and Controlword.

  For single use of the scaling function connect only the fb and cmd pins from position or velocity.

  If no Drives homing is needed, let the Pins unconnected.

