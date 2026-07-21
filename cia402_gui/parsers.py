from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Dict, List

from .model import DataType, Direction, Node, Parameter, Port


DECLARATION_RE = re.compile(
    r'^\s*(pin|param)\s+(in|out|io|r|rw)\s+(bit|float|signed|unsigned|s32|u32)\s+'
    r'([A-Za-z_][A-Za-z0-9_]*)[^;]*?(?:"([^"]*)")?\s*;'
)


def hal_name(c_identifier: str) -> str:
    return c_identifier.replace("_", "-")


def parse_comp(path: Path, instance_count: int = 1) -> List[Node]:
    text = Path(path).read_text(encoding="utf-8")
    declarations = text.split(";;", 1)[0]
    pin_specs = []
    parameter_specs = []
    for line in declarations.splitlines():
        match = DECLARATION_RE.match(line)
        if not match:
            continue
        kind, direction, raw_type, raw_name, description = match.groups()
        name = hal_name(raw_name)
        spec = (name, raw_name, DataType.parse(raw_type), description or "")
        if kind == "pin":
            pin_specs.append((name, Direction(direction), DataType.parse(raw_type), description or ""))
        else:
            parameter_specs.append(spec)

    defaults: Dict[str, str] = {}
    if ";;" in text:
        implementation = text.split(";;", 1)[1]
        parameter_names = {raw_name for _, raw_name, _, _ in parameter_specs}
        for raw_name in parameter_names:
            assignment = re.search(
                r"^\s*%s\s*=\s*([^;]+);" % re.escape(raw_name),
                implementation,
                flags=re.MULTILINE,
            )
            if assignment:
                defaults[raw_name] = assignment.group(1).strip()

    nodes: List[Node] = []
    for instance in range(instance_count):
        prefix = "cia402.%d" % instance
        node = Node(node_id=prefix, title=prefix, kind="cia402")
        for name, direction, data_type, description in pin_specs:
            node.ports[name] = Port(
                name=name,
                full_name="%s.%s" % (prefix, name),
                direction=direction,
                data_type=data_type,
                description=description,
            )
        for name, raw_name, data_type, description in parameter_specs:
            node.parameters[name] = Parameter(
                name=name,
                full_name="%s.%s" % (prefix, name),
                data_type=data_type,
                value=defaults.get(raw_name, "0"),
                description=description,
            )
        nodes.append(node)
    return nodes


def parse_ethercat_xml(path: Path) -> List[Node]:
    root = ET.parse(str(path)).getroot()
    nodes: List[Node] = []
    for master_position, master in enumerate(root.findall(".//master")):
        master_index = master.get("idx", str(master_position))
        for slave_position, slave in enumerate(master.findall("./slave")):
            slave_index = slave.get("idx", str(slave_position))
            prefix = "lcec.%s.%s" % (master_index, slave_index)
            title = "%s  (%s)" % (prefix, slave.get("type", "slave"))
            node = Node(node_id=prefix, title=title, kind="ethercat")
            for sync_manager in slave.findall("./syncManager"):
                sync_direction = sync_manager.get("dir", "").lower()
                # EtherCAT direction is relative to the slave. HAL direction is
                # relative to LinuxCNC, therefore it is the opposite.
                hal_direction = (
                    Direction.OUTPUT if sync_direction == "in" else Direction.INPUT
                )
                for entry in sync_manager.findall("./pdo/pdoEntry"):
                    pin_name = entry.get("halPin")
                    if not pin_name:
                        continue
                    data_type = DataType.parse(entry.get("halType", "unknown"))
                    index = entry.get("idx", "????")
                    subindex = entry.get("subIdx", "00")
                    bits = entry.get("bitLen", "?")
                    description = "PDO 0x%s:%s, %s bits" % (index, subindex, bits)
                    node.ports[pin_name] = Port(
                        name=pin_name,
                        full_name="%s.%s" % (prefix, pin_name),
                        direction=hal_direction,
                        data_type=data_type,
                        description=description,
                    )
            nodes.append(node)
    return nodes


JOINT_PORTS = (
    ("acc-cmd", Direction.OUTPUT, DataType.FLOAT, "Commanded acceleration (debug)"),
    ("active", Direction.OUTPUT, DataType.BIT, "Joint is active (debug)"),
    ("amp-enable-out", Direction.OUTPUT, DataType.BIT, "Amplifier enable"),
    ("amp-fault-in", Direction.INPUT, DataType.BIT, "Amplifier fault input"),
    ("backlash-corr", Direction.OUTPUT, DataType.FLOAT, "Raw backlash correction (debug)"),
    ("backlash-filt", Direction.OUTPUT, DataType.FLOAT, "Filtered backlash correction (debug)"),
    ("backlash-vel", Direction.OUTPUT, DataType.FLOAT, "Backlash correction velocity (debug)"),
    ("coarse-pos-cmd", Direction.OUTPUT, DataType.FLOAT, "Coarse position command (debug)"),
    ("error", Direction.OUTPUT, DataType.BIT, "Joint error (debug)"),
    ("f-error", Direction.OUTPUT, DataType.FLOAT, "Following error (debug)"),
    ("f-error-lim", Direction.OUTPUT, DataType.FLOAT, "Following-error limit (debug)"),
    ("f-errored", Direction.OUTPUT, DataType.BIT, "Following-error exceeded (debug)"),
    ("faulted", Direction.OUTPUT, DataType.BIT, "Joint faulted (debug)"),
    ("free-pos-cmd", Direction.OUTPUT, DataType.FLOAT, "Free-planner position command (debug)"),
    ("free-tp-enable", Direction.OUTPUT, DataType.BIT, "Free planner enabled (debug)"),
    ("free-vel-lim", Direction.OUTPUT, DataType.FLOAT, "Free-planner velocity limit (debug)"),
    ("home-state", Direction.OUTPUT, DataType.S32, "Homing state (debug)"),
    ("home-sw-in", Direction.INPUT, DataType.BIT, "Home switch"),
    ("homed", Direction.OUTPUT, DataType.BIT, "Joint is homed"),
    ("homing", Direction.OUTPUT, DataType.BIT, "Joint is homing"),
    ("in-position", Direction.OUTPUT, DataType.BIT, "Joint is in position"),
    ("index-enable", Direction.IO, DataType.BIT, "Encoder index handshake"),
    ("is-unlocked", Direction.INPUT, DataType.BIT, "Rotary joint unlocked input"),
    ("jog-accel-fraction", Direction.INPUT, DataType.FLOAT, "Jog acceleration fraction"),
    ("jog-counts", Direction.INPUT, DataType.S32, "Jog-wheel counts"),
    ("jog-enable", Direction.INPUT, DataType.BIT, "Enable jog wheel"),
    ("jog-scale", Direction.INPUT, DataType.FLOAT, "Distance per jog count"),
    ("jog-vel-mode", Direction.INPUT, DataType.BIT, "Jog wheel velocity mode"),
    ("kb-jog-active", Direction.OUTPUT, DataType.BIT, "Keyboard jog is active (debug)"),
    ("motor-offset", Direction.OUTPUT, DataType.FLOAT, "Motor home offset (debug)"),
    ("motor-pos-cmd", Direction.OUTPUT, DataType.FLOAT, "Motor position command"),
    ("motor-pos-fb", Direction.INPUT, DataType.FLOAT, "Motor position feedback"),
    ("neg-hard-limit", Direction.OUTPUT, DataType.BIT, "Negative hard limit (debug)"),
    ("neg-lim-sw-in", Direction.INPUT, DataType.BIT, "Negative limit switch"),
    ("pos-cmd", Direction.OUTPUT, DataType.FLOAT, "Joint position command"),
    ("pos-fb", Direction.OUTPUT, DataType.FLOAT, "Joint position feedback"),
    ("pos-hard-limit", Direction.OUTPUT, DataType.BIT, "Positive hard limit (debug)"),
    ("pos-lim-sw-in", Direction.INPUT, DataType.BIT, "Positive limit switch"),
    ("unlock", Direction.OUTPUT, DataType.BIT, "Rotary joint unlock request"),
    ("vel-cmd", Direction.OUTPUT, DataType.FLOAT, "Joint velocity command (debug)"),
    ("wheel-jog-active", Direction.OUTPUT, DataType.BIT, "Wheel jog is active (debug)"),
)


def create_joint_nodes(count: int) -> List[Node]:
    nodes: List[Node] = []
    for index in range(count):
        prefix = "joint.%d" % index
        node = Node(node_id=prefix, title=prefix, kind="joint")
        for name, direction, data_type, description in JOINT_PORTS:
            node.ports[name] = Port(
                name=name,
                full_name="%s.%s" % (prefix, name),
                direction=direction,
                data_type=data_type,
                description=description,
            )
        nodes.append(node)
    return nodes
