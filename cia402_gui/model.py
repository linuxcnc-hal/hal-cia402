from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Dict, List, Optional, Tuple


class Direction(str, Enum):
    INPUT = "in"
    OUTPUT = "out"
    IO = "io"


class DataType(str, Enum):
    BIT = "bit"
    S32 = "s32"
    U32 = "u32"
    FLOAT = "float"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: str) -> "DataType":
        aliases = {
            "signed": cls.S32,
            "unsigned": cls.U32,
            "hal_s32": cls.S32,
            "hal_u32": cls.U32,
            "hal_float": cls.FLOAT,
            "hal_bit": cls.BIT,
        }
        normalized = value.strip().lower()
        return aliases.get(normalized, cls._value2member_map_.get(normalized, cls.UNKNOWN))


@dataclass
class Port:
    name: str
    full_name: str
    direction: Direction
    data_type: DataType
    description: str = ""


@dataclass
class Parameter:
    name: str
    full_name: str
    data_type: DataType
    value: str = "0"
    description: str = ""


@dataclass
class Node:
    node_id: str
    title: str
    kind: str
    ports: Dict[str, Port] = field(default_factory=dict)
    parameters: Dict[str, Parameter] = field(default_factory=dict)
    position: Tuple[float, float] = (0.0, 0.0)
    active: bool = True


@dataclass
class Signal:
    name: str
    source: str
    destinations: List[str] = field(default_factory=list)


class WiringError(ValueError):
    pass


class WiringProject:
    SIGNAL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}
        self.signals: Dict[str, Signal] = {}
        self.source_files: Dict[str, str] = {}

    @property
    def ports(self) -> Dict[str, Port]:
        return {
            port.full_name: port
            for node in self.nodes.values()
            if node.active
            for port in node.ports.values()
        }

    @property
    def all_ports(self) -> Dict[str, Port]:
        return {
            port.full_name: port
            for node in self.nodes.values()
            for port in node.ports.values()
        }

    def add_node(self, node: Node) -> None:
        if node.node_id in self.nodes:
            raise WiringError("Duplicate node: %s" % node.node_id)
        duplicate_ports = set(self.all_ports).intersection(p.full_name for p in node.ports.values())
        if duplicate_ports:
            raise WiringError("Duplicate ports: %s" % ", ".join(sorted(duplicate_ports)))
        self.nodes[node.node_id] = node

    def _signal_for_port(self, full_name: str) -> Optional[Signal]:
        for signal in self.signals.values():
            if signal.source == full_name or full_name in signal.destinations:
                return signal
        return None

    def connect(self, first: str, second: str, signal_name: Optional[str] = None) -> Signal:
        ports = self.ports
        if first not in ports or second not in ports:
            raise WiringError("Both connection endpoints must be existing ports")
        if first == second:
            raise WiringError("A port cannot be connected to itself")

        a, b = ports[first], ports[second]
        if a.data_type != b.data_type:
            raise WiringError(
                "Type mismatch: %s is %s, but %s is %s"
                % (a.full_name, a.data_type.value, b.full_name, b.data_type.value)
            )

        source, destination = self._orient(a, b)
        destination_signal = self._signal_for_port(destination.full_name)
        if destination_signal:
            raise WiringError(
                "%s is already connected to signal %s"
                % (destination.full_name, destination_signal.name)
            )

        existing = self._signal_for_port(source.full_name)
        if existing:
            if existing.source != source.full_name:
                raise WiringError("A HAL signal can have only one writer")
            existing.destinations.append(destination.full_name)
            return existing

        name = signal_name or self.suggest_signal_name(source)
        self.validate_signal_name(name)
        if name in self.signals:
            raise WiringError("Signal name already exists: %s" % name)
        signal = Signal(name=name, source=source.full_name, destinations=[destination.full_name])
        self.signals[name] = signal
        return signal

    @staticmethod
    def _orient(a: Port, b: Port) -> Tuple[Port, Port]:
        if a.direction == Direction.OUTPUT and b.direction != Direction.OUTPUT:
            return a, b
        if b.direction == Direction.OUTPUT and a.direction != Direction.OUTPUT:
            return b, a
        if a.direction == Direction.IO and b.direction == Direction.INPUT:
            return a, b
        if b.direction == Direction.IO and a.direction == Direction.INPUT:
            return b, a
        if a.direction == Direction.IO and b.direction == Direction.IO:
            return a, b
        if a.direction == Direction.OUTPUT and b.direction == Direction.OUTPUT:
            raise WiringError("Cannot connect two output pins")
        raise WiringError("A connection needs an output or bidirectional writer")

    @classmethod
    def validate_signal_name(cls, name: str) -> None:
        if not cls.SIGNAL_NAME.match(name):
            raise WiringError("Invalid HAL signal name: %s" % name)

    def suggest_signal_name(self, source: Port) -> str:
        base = source.full_name.replace("joint.", "j").replace("cia402.", "cia402-")
        base = base.replace("lcec.", "ec-").replace(".", "-")
        candidate = base
        suffix = 2
        while candidate in self.signals:
            candidate = "%s-%d" % (base, suffix)
            suffix += 1
        return candidate

    def remove_signal(self, name: str) -> None:
        self.signals.pop(name, None)

    def deactivate_node(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise WiringError("Unknown block: %s" % node_id)
        self.nodes[node_id].active = False

    def activate_node(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise WiringError("Unknown block: %s" % node_id)
        self.nodes[node_id].active = True

    def active_signals(self) -> List[Signal]:
        active_ports = self.ports
        result: List[Signal] = []
        for signal in self.signals.values():
            if signal.source not in active_ports:
                continue
            destinations = [
                destination
                for destination in signal.destinations
                if destination in active_ports
            ]
            if destinations:
                result.append(
                    Signal(
                        name=signal.name,
                        source=signal.source,
                        destinations=destinations,
                    )
                )
        return result

    def rename_signal(self, old_name: str, new_name: str) -> None:
        if old_name not in self.signals:
            raise WiringError("Unknown signal: %s" % old_name)
        self.validate_signal_name(new_name)
        if new_name != old_name and new_name in self.signals:
            raise WiringError("Signal name already exists: %s" % new_name)
        signal = self.signals.pop(old_name)
        signal.name = new_name
        self.signals[new_name] = signal

    def validate(self) -> List[str]:
        errors: List[str] = []
        ports = self.ports
        for signal in self.active_signals():
            try:
                self.validate_signal_name(signal.name)
            except WiringError as exc:
                errors.append(str(exc))
            if signal.source not in ports:
                errors.append("Signal %s has a missing source" % signal.name)
                continue
            source = ports[signal.source]
            if source.direction == Direction.INPUT:
                errors.append("Signal %s source is an input pin" % signal.name)
            for destination_name in signal.destinations:
                destination = ports.get(destination_name)
                if destination is None:
                    errors.append("Signal %s has a missing destination" % signal.name)
                elif destination.direction == Direction.OUTPUT:
                    errors.append("Signal %s drives an output pin" % signal.name)
                elif destination.data_type != source.data_type:
                    errors.append("Signal %s contains mixed HAL types" % signal.name)
        return errors

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "source_files": self.source_files,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "title": node.title,
                    "kind": node.kind,
                    "position": list(node.position),
                    "active": node.active,
                    "ports": [
                        {
                            "name": p.name,
                            "full_name": p.full_name,
                            "direction": p.direction.value,
                            "data_type": p.data_type.value,
                            "description": p.description,
                        }
                        for p in node.ports.values()
                    ],
                    "parameters": [
                        {
                            "name": p.name,
                            "full_name": p.full_name,
                            "data_type": p.data_type.value,
                            "value": p.value,
                            "description": p.description,
                        }
                        for p in node.parameters.values()
                    ],
                }
                for node in self.nodes.values()
            ],
            "signals": [
                {
                    "name": signal.name,
                    "source": signal.source,
                    "destinations": signal.destinations,
                }
                for signal in self.signals.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WiringProject":
        project = cls()
        project.source_files = dict(data.get("source_files", {}))
        for raw_node in data.get("nodes", []):
            node = Node(
                node_id=raw_node["node_id"],
                title=raw_node["title"],
                kind=raw_node["kind"],
                position=tuple(raw_node.get("position", (0.0, 0.0))),
                active=bool(raw_node.get("active", True)),
            )
            for raw_port in raw_node.get("ports", []):
                port = Port(
                    name=raw_port["name"],
                    full_name=raw_port["full_name"],
                    direction=Direction(raw_port["direction"]),
                    data_type=DataType.parse(raw_port["data_type"]),
                    description=raw_port.get("description", ""),
                )
                node.ports[port.name] = port
            for raw_parameter in raw_node.get("parameters", []):
                parameter = Parameter(
                    name=raw_parameter["name"],
                    full_name=raw_parameter["full_name"],
                    data_type=DataType.parse(raw_parameter["data_type"]),
                    value=str(raw_parameter.get("value", "0")),
                    description=raw_parameter.get("description", ""),
                )
                node.parameters[parameter.name] = parameter
            project.add_node(node)
        for raw_signal in data.get("signals", []):
            signal = Signal(
                name=raw_signal["name"],
                source=raw_signal["source"],
                destinations=list(raw_signal.get("destinations", [])),
            )
            project.signals[signal.name] = signal
        return project
