from __future__ import annotations

from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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


LIVE_PIN_RE = re.compile(r"^(joint|cia402)\.(\d+)\.(.+)$")


def _live_type(value, hal_module=None) -> DataType:
    if isinstance(value, str):
        return DataType.parse(value)
    if hal_module is not None:
        mapping = {
            getattr(hal_module, "HAL_BIT", object()): DataType.BIT,
            getattr(hal_module, "HAL_S32", object()): DataType.S32,
            getattr(hal_module, "HAL_U32", object()): DataType.U32,
            getattr(hal_module, "HAL_FLOAT", object()): DataType.FLOAT,
        }
        return mapping.get(value, DataType.UNKNOWN)
    return DataType.UNKNOWN


def _live_direction(value, hal_module=None) -> Optional[Direction]:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("/", "")
        return {
            "in": Direction.INPUT,
            "out": Direction.OUTPUT,
            "io": Direction.IO,
        }.get(normalized)
    if hal_module is not None:
        mapping = {
            getattr(hal_module, "HAL_IN", object()): Direction.INPUT,
            getattr(hal_module, "HAL_OUT", object()): Direction.OUTPUT,
            getattr(hal_module, "HAL_IO", object()): Direction.IO,
        }
        return mapping.get(value)
    return None


def _info_value(info: dict, name: str, default=None):
    return info.get(name, info.get(name.lower(), default))


def _nodes_from_pin_info(pin_info: Iterable[dict], hal_module=None) -> List[Node]:
    nodes: Dict[str, Node] = {}
    for info in pin_info:
        full_name = str(_info_value(info, "NAME", ""))
        match = LIVE_PIN_RE.match(full_name)
        if not match:
            continue
        kind, instance, port_name = match.groups()
        direction = _live_direction(_info_value(info, "DIRECTION"), hal_module)
        if direction is None:
            continue
        node_id = "%s.%s" % (kind, instance)
        node = nodes.setdefault(
            node_id,
            Node(node_id=node_id, title=node_id, kind="joint" if kind == "joint" else "cia402"),
        )
        node.ports[port_name] = Port(
            name=port_name,
            full_name=full_name,
            direction=direction,
            data_type=_live_type(_info_value(info, "TYPE"), hal_module),
            description="Live LinuxCNC HAL pin",
        )
    return sorted(nodes.values(), key=lambda node: (node.kind, int(node.node_id.split(".")[1])))


def _discover_with_python_hal() -> Tuple[List[Node], Dict[str, Tuple[DataType, str]]]:
    import hal  # Available when running on a LinuxCNC installation.

    nodes = _nodes_from_pin_info(hal.get_info_pins(), hal)
    parameters: Dict[str, Tuple[DataType, str]] = {}
    if hasattr(hal, "get_info_params"):
        for info in hal.get_info_params():
            full_name = str(_info_value(info, "NAME", ""))
            if not full_name.startswith("cia402."):
                continue
            data_type = _live_type(_info_value(info, "TYPE"), hal)
            value = _info_value(info, "VALUE", "0")
            if isinstance(value, bool):
                value = "1" if value else "0"
            parameters[full_name] = (data_type, str(value))
    return nodes, parameters


def _halcmd_rows(item_type: str, pattern: Optional[str] = None) -> List[List[str]]:
    command = ["halcmd", "-s", "show", item_type]
    if pattern:
        command.append(pattern)
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return [line.split() for line in result.stdout.splitlines() if line.strip()]


def _discover_with_halcmd() -> Tuple[List[Node], Dict[str, Tuple[DataType, str]]]:
    pin_info = []
    for row in _halcmd_rows("pin"):
        name_index = next(
            (index for index, token in enumerate(row) if LIVE_PIN_RE.match(token)),
            None,
        )
        if name_index is None:
            continue
        before_name = row[:name_index]
        raw_type = next(
            (token for token in before_name if DataType.parse(token) != DataType.UNKNOWN),
            "unknown",
        )
        raw_direction = next(
            (token for token in before_name if token.upper().replace("/", "") in ("IN", "OUT", "IO")),
            "",
        )
        pin_info.append(
            {"NAME": row[name_index], "TYPE": raw_type, "DIRECTION": raw_direction}
        )

    parameters: Dict[str, Tuple[DataType, str]] = {}
    for row in _halcmd_rows("param"):
        name_index = next(
            (index for index, token in enumerate(row) if token.startswith("cia402.")),
            None,
        )
        if name_index is None:
            continue
        before_name = row[:name_index]
        raw_type = next(
            (token for token in before_name if DataType.parse(token) != DataType.UNKNOWN),
            "unknown",
        )
        value = before_name[-1] if before_name else "0"
        parameters[row[name_index]] = (DataType.parse(raw_type), value)
    return _nodes_from_pin_info(pin_info), parameters


def discover_live_hal() -> Tuple[List[Node], Dict[str, Tuple[DataType, str]]]:
    """Discover joint and cia402 objects from a running LinuxCNC HAL.

    The in-process LinuxCNC Python module is preferred. Older installations
    are supported through script-friendly ``halcmd`` output. An unavailable
    or stopped HAL is represented by an empty result so the GUI can still be
    used as an offline editor.
    """

    try:
        return _discover_with_python_hal()
    except Exception:
        pass
    try:
        return _discover_with_halcmd()
    except Exception:
        return [], {}


def attach_component_parameters(
    nodes: Sequence[Node], comp_path: Path, live_parameters: Dict[str, Tuple[DataType, str]]
) -> None:
    cia_nodes = [node for node in nodes if node.kind == "cia402"]
    if not cia_nodes:
        return
    maximum_index = max(int(node.node_id.split(".")[1]) for node in cia_nodes)
    templates = {node.node_id: node for node in parse_comp(comp_path, maximum_index + 1)}
    for node in cia_nodes:
        template = templates[node.node_id]
        node.parameters = template.parameters
        for parameter in node.parameters.values():
            live = live_parameters.get(parameter.full_name)
            if live:
                live_type, live_value = live
                if live_type != DataType.UNKNOWN:
                    parameter.data_type = live_type
                parameter.value = live_value
