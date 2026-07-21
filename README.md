# LinuxCNC HAL CiA 402

`hal-cia402` is a LinuxCNC realtime HAL component and graphical wiring editor
for CiA 402 servo drives. The component sits between LinuxCNC motion and a
fieldbus driver such as LinuxCNC EtherCAT (`lcec`), providing:

- CiA 402 state-machine control
- Cyclic Synchronous Position (CSP) and Velocity (CSV) operation
- Position and velocity scaling
- Drive feedback and fault reporting
- Drive-internal homing support
- A block-based editor for generating HAL signal connections

The fieldbus transport is intentionally separate from this component. EtherCAT,
CANopen, or another HAL driver supplies the raw drive objects; `cia402` converts
them to the pins normally used by LinuxCNC motion.

```text
fieldbus read -> cia402.read-all -> LinuxCNC motion/PID
LinuxCNC motion/PID -> cia402.write-all -> fieldbus write
```

> **Safety:** Test generated HAL configuration with the machine unable to move.
> Verify scaling, feedback direction, limits, enable behavior, and emergency-stop
> operation before applying power to an axis.

## Requirements

- LinuxCNC with development tools, including `halcompile`
- A configured hardware HAL driver, such as LinuxCNC EtherCAT
- Python 3.8 or newer and PyQt5 for the graphical editor

On a Debian-based LinuxCNC installation, install the GUI dependency with:

```bash
sudo apt update
sudo apt install python3-pyqt5
```

## Install the realtime component

Clone the repository and install `cia402.comp` using LinuxCNC's component
compiler:

```bash
git clone https://github.com/linuxcnc-hal/hal-cia402.git
cd hal-cia402
sudo halcompile --install cia402.comp
```

Load one instance per drive in your HAL configuration:

```hal
loadrt cia402 count=3
```

The component exports `cia402.N.read-all` and `cia402.N.write-all`. Add them to
the servo thread around the motion controller and any PID calculations:

```hal
addf lcec.read-all servo-thread

addf cia402.0.read-all servo-thread
addf cia402.1.read-all servo-thread
addf cia402.2.read-all servo-thread

addf motion-command-handler servo-thread
addf motion-controller servo-thread
# Add PID or other control functions here when required.

addf cia402.0.write-all servo-thread
addf cia402.1.write-all servo-thread
addf cia402.2.write-all servo-thread

addf lcec.write-all servo-thread
```

See [example/cia402.hal](example/cia402.hal) for a complete three-axis snippet.

## Graphical HAL wiring editor

The included editor provides a Simulink-style view of LinuxCNC joints, `cia402`
instances, and the PDO pins declared in `ethercat-conf.xml`. It validates HAL
types and signal direction, prevents multiple writers, and exports a
deterministic `.hal` file.

Start LinuxCNC first so its live HAL pins are available, then run the editor
from the repository root:

```bash
python3 -m cia402_gui
```

The canvas starts empty. Discovered blocks are organized in the **Block
library** as:

- **LinuxCNC joints** — motion command, feedback, enable, and homing pins
- **CiA 402 drive interface** — state control, scaling, drive data, and modes
- **EtherCAT / LCEC** — PDO communication with each physical slave
- **Other** — additional discovered HAL components

The normal data path is:

```text
LinuxCNC joint <-> CiA 402 interface <-> EtherCAT/LCEC
```

### Basic workflow

1. Choose **File > Import EtherCAT XML** and select `ethercat-conf.xml`.
2. Double-click blocks in the **Block library** to add them to the canvas.
3. Select a block and add only the required pins from **Block signals**.
4. Drag between compatible pins and enter a HAL signal name.
5. Double-click a block to inspect and edit its writable parameters.
6. Choose **File > Preview HAL** to review the result.
7. Choose **File > Export HAL** to create `cia402-generated.hal`.

Load the generated connections from your main HAL configuration:

```hal
source cia402-generated.hal
```

Keep component loading and realtime thread ordering in the main configuration;
the generated file contains signal connections and writable parameter values.

### Live discovery and XML import

The editor discovers loaded `joint.N.*`, `cia402.N.*`, and related parameters
through the LinuxCNC Python HAL API, with `halcmd` as a fallback. Press **F5**
to refresh live HAL objects.

Importing EtherCAT XML adds its `lcec.M.S` blocks without duplicating existing
ones. Blocks are not placed on the canvas automatically. Re-importing or
reloading XML also preserves the current placement, selected pins, and blocks
that were intentionally removed from the canvas.

For offline use, an XML file and fallback counts can be supplied explicitly:

```bash
python3 -m cia402_gui \
    --xml /path/to/ethercat-conf.xml \
    --comp /path/to/cia402.comp \
    --joints 3 \
    --instances 3
```

### Blocks, pins, and parameters

New blocks initially have no visible pins. Select a block to open **Block
signals**, where pins are grouped by direction and HAL type (`bit`, `float`,
`s32`, `u32`, `s64`, and `u64`). Add or remove individual pins as needed.

Removing a connected pin suspends its connection; adding the pin again restores
it. Removing a block from the canvas works the same way. The block remains in
the library and can be added again later without losing its wiring.

To change a component prefix after moving an EtherCAT slave or LinuxCNC
instance, right-click its block and choose **Rename block**, or select it and
press **F2**. Renaming `lcec.0.1` to `lcec.0.2`, for example, updates every pin,
parameter, and existing wiring endpoint belonging to that block. HAL net names
remain unchanged. If `lcec.0.2` already exists, the two blocks automatically
exchange names and all wiring is updated atomically. Blocks in different
categories are not swapped, and invalid HAL block names are rejected.

When a layout is arranged, right-click a block and choose **Lock block
position** to prevent accidental movement. Locked blocks remain selectable and
their pins remain fully usable for wiring. Right-click again and choose
**Unlock block position** to move the block. Lock states are saved in projects.

Double-click a block to open its searchable parameter view. Writable values are
marked `RW` and may be edited; read-only values are marked `RO`. Only writable
parameters are emitted as `setp` commands during export.

### Goto/From routing

Long wires can be represented with Simulink-style Goto/From tags:

1. Right-click an output pin and select **Create Goto signal**.
2. Enter the HAL signal name.
3. Right-click a compatible input and select **Connect from Goto**.
4. Select the named signal.

Once assigned, that Goto signal is removed from other **Connect from Goto**
lists to prevent accidental reuse. Goto/From is only a visual representation;
HAL export writes an ordinary `net` connection.

Direct wires can be converted to Goto/From display from their context menu.
Deleting a Goto removes the entire HAL signal and all associated From tags.
Deleting a From or a direct wire disconnects only that destination; if it was
the final destination, the unused HAL signal is removed automatically.

### Projects

Use **File > Save Project** to store the canvas as JSON and **File > Open
Project** to restore it. Projects retain block positions, selected pins,
parameter edits, signal routes, and suspended connections.

### Visual notes

Right-click an empty area of the canvas and choose **Add visual note here**, or
use **Wiring > Add visual note** (`Ctrl+Shift+N`), to place a small comment near
the blocks. Notes can be moved, double-clicked to edit, and removed with
**Delete** or their context menu. They are saved in the JSON project for future
reference but are ignored by HAL validation, preview, and export.

## Manual HAL wiring example

The following example connects one LinuxCNC joint through `cia402.0` to one
EtherCAT slave. PDO names vary by EtherCAT configuration, so use the names
exported by your own `ethercat-conf.xml`.

```hal
# Component configuration
setp cia402.0.csp-mode 1
setp cia402.0.pos-scale 10000000

# LinuxCNC motion <-> CiA 402
net x-enable  joint.0.amp-enable-out => cia402.0.enable
net x-pos-cmd joint.0.motor-pos-cmd  => cia402.0.pos-cmd
net x-pos-fb  cia402.0.pos-fb        => joint.0.motor-pos-fb

# EtherCAT drive -> CiA 402
net x-statusword lcec.0.0.statusword      => cia402.0.statusword
net x-mode-fb    lcec.0.0.mode-display    => cia402.0.opmode-display
net x-actual-pos lcec.0.0.actual-position => cia402.0.drv-actual-position
net x-actual-vel lcec.0.0.actual-velocity => cia402.0.drv-actual-velocity

# CiA 402 -> EtherCAT drive
net x-controlword cia402.0.controlword         => lcec.0.0.controlword
net x-mode-cmd    cia402.0.opmode              => lcec.0.0.mode-of-operation
net x-target-pos  cia402.0.drv-target-position => lcec.0.0.target-position
net x-target-vel  cia402.0.drv-target-velocity => lcec.0.0.target-velocity
```

## Operation modes

The component starts in CSP mode by default:

```hal
setp cia402.0.csp-mode 1
```

Select CSV mode before LinuxCNC starts by setting:

```hal
setp cia402.0.csp-mode 0
```

Runtime switching between CSP and CSV is intentionally unsupported.

The available writable component parameters are:

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `pos-scale` | `1.0` | Drive position increments per machine unit |
| `velo-scale` | `1.0` | Drive velocity units per machine velocity unit |
| `auto-fault-reset` | `1` | Reset a drive fault automatically on the next enable edge |
| `csp-mode` | `1` | `1` for CSP, `0` for CSV; read at component startup |

## Drive-internal homing

To use the drive's homing procedure, configure the joint for index-only homing
and connect `cia402.N.home` to `joint.N.index-enable`:

```ini
HOME_SEARCH_VEL = 0.0
HOME_LATCH_VEL = 0.2
HOME_USE_INDEX = TRUE
```

`HOME_LATCH_VEL` must be non-zero; the drive's own configuration determines the
actual homing speed. If a PID component is used, include its `index-enable` pin
in the same HAL signal.

## Development

The parser, model, project serialization, validation, and HAL generator tests do
not require Qt:

```bash
python3 -m unittest discover -s tests -v
```

To install the editor as a Python package during development:

```bash
python3 -m pip install -e .
cia402-wiring-gui
```

<h2>Support this project</h2>

<p>
  If <code>hal-cia402</code> saves you time or helps with your LinuxCNC setup,
  you can support its continued development:
</p>

<a href="https://www.buymeacoffee.com/eshamsaki">
  <img src="https://img.shields.io/badge/Buy_Me_a_Coffee-Support_Development-FFDD00?style=for-the-badge&amp;logo=buy-me-a-coffee&amp;logoColor=000000" alt="Buy Me a Coffee">
</a>

## License

This project is distributed under the GNU General Public License. See
[LICENSE](LICENSE) for details.
