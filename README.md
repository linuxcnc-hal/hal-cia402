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
* Searchable live parameter editing for joint, `cia402`, and `lcec` blocks.
* Project save/load using JSON and deterministic `.hal` file export.
* Reversible block removal with an Available Blocks palette.
* Per-block signal selection so blocks start compact and only show requested pins.

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

The canvas starts empty. Every discovered block is placed in the categorized
**Block library** instead:

* **LinuxCNC joints** — motion commands, amplifier control, and feedback.
* **CiA 402 drive interface** — drive state control, scaling, modes, and homing.
* **EtherCAT / LCEC** — PDO communication with the physical slave.

This presents the intended path as `LinuxCNC joint -> CiA 402 -> EtherCAT/LCEC`.
Double-click an available block to add it to the canvas. The library continues
to show added blocks with an **on canvas** status; double-clicking one focuses
it. Importing XML adds blocks to the library without placing them automatically.

### Goto/From signal routing

Long connections can be displayed using Simulink-style Goto/From tags:

1. Right-click an output pin and choose **Create Goto signal**.
2. Enter the HAL signal name.
3. Right-click a compatible input pin and choose **Connect from Goto**.
4. Select the named signal.

The canvas displays a short wire to `Goto <signal>` at the writer and a short
wire from `From <signal>` at every reader. HAL export still emits one ordinary
`net` command containing the real source and destinations. Right-click a direct
wire to convert it to Goto/From display, or right-click a tag to return to a
direct wire. Goto/From display mode is saved with the project.

Goto and From tags can also be selected and removed with **Delete** or their
context menus. Deleting a Goto removes the complete HAL signal and all its From
tags. Deleting a From disconnects only that reader. Deleting a direct wire has
the same per-reader behavior. If the final reader is disconnected, the unused
HAL signal is deleted automatically; fan-out signals remain while at least one
reader is still connected.

An XML file can still be opened directly from the command line:

```bash
python3 -m cia402_gui \
    --xml /path/to/ethercat-conf.xml \
    --comp /path/to/cia402.comp \
    --joints 3 \
    --instances 3
```

Drag from one port to another and enter a HAL signal name. Double-click any
block to inspect all parameters discovered from the running HAL. Writable
parameters are marked `RW` and can be edited; read-only parameters are marked
`RO` and shown for reference. The parameter dialog includes search, and HAL
export emits `setp` only for writable parameters. Use **File > Preview HAL**
to inspect the output and **File > Export HAL** to write
`cia402-generated.hal`.

Newly discovered and imported blocks initially show no signals. Select a block
to open the **Block signals** panel, search its available pins, and double-click
or press **Add selected** to place only the pins you need on the block. Remove
shown pins from the same panel, or right-click a pin and choose **Remove signal
from block**. If a connected pin is removed, its HAL connection is suspended
and is restored when the pin is added again. Older saved projects retain their
existing visible-pin layout. Signal lists are grouped first by direction
(Inputs, Outputs, and Bidirectional) and then by HAL type (`bit`, `float`,
`s32`, `u32`, `s64`, and `u64`). Each group displays its signal count.

Select a block and press **Delete**, or right-click it and choose **Remove
block**, to remove it from the canvas. Removed blocks appear in the
**Available blocks** panel. Double-click one there to add it back. Connections
to a removed block are suspended and return when the block is restored.
Re-importing an EtherCAT XML file merges blocks by their `lcec.M.S` name, so
existing blocks are not duplicated and intentionally removed blocks stay in
the Available Blocks panel.

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

