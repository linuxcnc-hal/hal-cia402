from __future__ import annotations

from pathlib import Path
import sys
from typing import Dict, Optional

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    QT_BINDING = "PyQt5"
except ImportError:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        QT_BINDING = "PySide6"
    except ImportError as exc:
        raise ImportError(
            "A Qt binding is required. Install python3-pyqt5 on LinuxCNC "
            "or run: python -m pip install PyQt5"
        ) from exc

from .generator import generate_hal, write_hal
from .model import DataType, Direction, Node, Note, Port, WiringError, WiringProject
from .parsers import (
    attach_component_parameters,
    create_joint_nodes,
    discover_live_hal,
    parse_comp,
    parse_ethercat_xml,
)
from .project_io import load_project, save_project


# PyQt5 exposes QAction from QtWidgets, while PySide6 exposes it from QtGui.
# Avoid getattr(..., QtGui.QAction): Python evaluates that default eagerly,
# which raises on PyQt5 even though QtWidgets.QAction exists.
if hasattr(QtWidgets, "QAction"):
    QAction = QtWidgets.QAction
else:
    QAction = QtGui.QAction

TYPE_COLORS = {
    DataType.BIT: QtGui.QColor("#e3b341"),
    DataType.S32: QtGui.QColor("#58a6ff"),
    DataType.U32: QtGui.QColor("#a371f7"),
    DataType.S64: QtGui.QColor("#79c0ff"),
    DataType.U64: QtGui.QColor("#d2a8ff"),
    DataType.FLOAT: QtGui.QColor("#3fb950"),
    DataType.UNKNOWN: QtGui.QColor("#8b949e"),
}

NODE_COLORS = {
    "joint": QtGui.QColor("#1f6feb"),
    "cia402": QtGui.QColor("#8957e5"),
    "ethercat": QtGui.QColor("#238636"),
}


def _layout_and_add(project: WiringProject, nodes, x_position: float, vertical_spacing: float) -> None:
    for index, node in enumerate(nodes):
        node.position = (x_position, index * vertical_spacing)
        project.add_node(node)


def _hide_all_ports(nodes) -> None:
    for node in nodes:
        for port in node.ports.values():
            port.visible = False


def _put_all_blocks_in_library(nodes) -> None:
    for node in nodes:
        node.active = False


def build_runtime_project(comp_path: Path) -> WiringProject:
    """Create the initial canvas from a running LinuxCNC HAL."""

    project = WiringProject()
    project.source_files = {"component": str(Path(comp_path).resolve())}
    live_nodes, live_parameters = discover_live_hal()
    attach_component_parameters(live_nodes, comp_path, live_parameters)
    _hide_all_ports(live_nodes)
    _put_all_blocks_in_library(live_nodes)
    vertical_spacing = 140.0
    _layout_and_add(
        project, [node for node in live_nodes if node.kind == "joint"], 0.0, vertical_spacing
    )
    _layout_and_add(
        project, [node for node in live_nodes if node.kind == "cia402"], 380.0, vertical_spacing
    )
    return project


def build_project(
    xml_path: Path,
    comp_path: Path,
    joint_count: Optional[int] = None,
    instance_count: Optional[int] = None,
) -> WiringProject:
    """Build a canvas from live HAL pins plus one EtherCAT XML file."""

    project = WiringProject()
    project.source_files = {
        "ethercat_xml": str(Path(xml_path).resolve()),
        "component": str(Path(comp_path).resolve()),
    }
    ethercat_nodes = parse_ethercat_xml(xml_path)
    live_nodes, live_parameters = discover_live_hal()
    joints = [node for node in live_nodes if node.kind == "joint"]
    components = [node for node in live_nodes if node.kind == "cia402"]

    if not joints:
        inferred_joint_count = joint_count or max(1, len(ethercat_nodes))
        joints = create_joint_nodes(inferred_joint_count)
    if not components:
        inferred_instance_count = instance_count or max(1, len(joints), len(ethercat_nodes))
        components = parse_comp(comp_path, inferred_instance_count)

    all_nodes = joints + components + ethercat_nodes
    attach_component_parameters(all_nodes, comp_path, live_parameters)
    _hide_all_ports(all_nodes)
    _put_all_blocks_in_library(all_nodes)
    vertical_spacing = 140.0
    _layout_and_add(project, joints, 0.0, vertical_spacing)
    _layout_and_add(project, components, 380.0, vertical_spacing)
    _layout_and_add(project, ethercat_nodes, 760.0, vertical_spacing)
    return project


def merge_project(target: WiringProject, incoming: WiringProject) -> None:
    """Merge refreshed block definitions without duplicating canvas blocks."""

    for node_id, new_node in incoming.nodes.items():
        old_node = target.nodes.get(node_id)
        if old_node is None:
            target.add_node(new_node)
            continue
        new_node.active = old_node.active
        new_node.position = old_node.position
        new_node.locked = old_node.locked
        for name, port in new_node.ports.items():
            if name in old_node.ports:
                port.visible = old_node.ports[name].visible
        for name, parameter in new_node.parameters.items():
            if name in old_node.parameters:
                parameter.value = old_node.parameters[name].value
        target.nodes[node_id] = new_node
    target.source_files.update(incoming.source_files)


class PortItem(QtWidgets.QGraphicsEllipseItem):
    RADIUS = 6.0

    def __init__(self, port: Port, node_item: "NodeItem") -> None:
        super().__init__(-self.RADIUS, -self.RADIUS, self.RADIUS * 2, self.RADIUS * 2, node_item)
        self.port = port
        self.node_item = node_item
        self.setBrush(QtGui.QBrush(TYPE_COLORS[port.data_type]))
        self.setPen(QtGui.QPen(QtGui.QColor("#f0f6fc"), 1.0))
        self.setZValue(3)
        self.setToolTip(
            "%s\n%s %s\n%s"
            % (port.full_name, port.direction.value.upper(), port.data_type.value, port.description)
        )
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)

    def center(self) -> QtCore.QPointF:
        return self.scenePos()

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self.scene().begin_connection(self)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self.scene().update_connection(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.scene().finish_connection(event.scenePos())
        event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = QtWidgets.QMenu()
        existing = self.scene().project._signal_for_port(self.port.full_name)
        create_goto_action = None
        connect_from_action = None
        route_existing_action = None
        if self.port.direction in (Direction.OUTPUT, Direction.IO):
            if existing is None:
                create_goto_action = menu.addAction("Create Goto signal...")
            elif existing.source == self.port.full_name and not existing.routed:
                route_existing_action = menu.addAction("Display as Goto/From")
        if self.port.direction in (Direction.INPUT, Direction.IO) and existing is None:
            connect_from_action = menu.addAction("Connect from Goto...")
        if menu.actions():
            menu.addSeparator()
        remove_action = menu.addAction("Remove signal from block")
        selected = menu.exec(event.screenPos()) if hasattr(menu, "exec") else menu.exec_(event.screenPos())
        if selected == create_goto_action:
            self.scene().create_goto(self.port.full_name)
        elif selected == connect_from_action:
            self.scene().connect_from_goto(self.port.full_name)
        elif selected == route_existing_action and existing:
            self.scene().set_signal_routed(existing.name, True)
        elif selected == remove_action:
            self.scene().set_port_visible(
                self.node_item.node.node_id, self.port.name, False
            )
        event.accept()


class NodeItem(QtWidgets.QGraphicsRectItem):
    WIDTH = 310.0
    HEADER = 30.0
    ROW = 22.0

    def __init__(self, node: Node, editor: "WiringScene") -> None:
        visible_ports = [port for port in node.ports.values() if port.visible]
        height = self.HEADER + max(1, len(visible_ports)) * self.ROW + 10.0
        super().__init__(0.0, 0.0, self.WIDTH, height)
        self.node = node
        self.editor = editor
        self.port_items: Dict[str, PortItem] = {}
        self.setBrush(QtGui.QBrush(QtGui.QColor("#161b22")))
        self.setPen(QtGui.QPen(QtGui.QColor("#484f58"), 1.5))
        self.setFlags(
            QtWidgets.QGraphicsItem.ItemIsMovable
            | QtWidgets.QGraphicsItem.ItemIsSelectable
            | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(1)

        header = QtWidgets.QGraphicsRectItem(0, 0, self.WIDTH, self.HEADER, self)
        header.setBrush(QtGui.QBrush(NODE_COLORS.get(node.kind, QtGui.QColor("#30363d"))))
        header.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        title = QtWidgets.QGraphicsSimpleTextItem(node.title, self)
        title.setBrush(QtGui.QBrush(QtGui.QColor("white")))
        title.setPos(10, 6)
        if node.locked:
            lock_label = QtWidgets.QGraphicsSimpleTextItem("LOCKED", self)
            lock_label.setBrush(QtGui.QBrush(QtGui.QColor("white")))
            lock_label.setPos(
                self.WIDTH - lock_label.boundingRect().width() - 10.0, 6.0
            )

        if not visible_ports:
            empty_label = QtWidgets.QGraphicsSimpleTextItem("No signals selected", self)
            empty_label.setBrush(QtGui.QBrush(QtGui.QColor("#8b949e")))
            empty_label.setPos(12.0, self.HEADER + 5.0)

        for row, port in enumerate(visible_ports):
            y = self.HEADER + 10.0 + row * self.ROW
            port_item = PortItem(port, self)
            label = QtWidgets.QGraphicsSimpleTextItem(
                "%s  [%s]" % (port.name, port.data_type.value), self
            )
            label.setBrush(QtGui.QBrush(QtGui.QColor("#c9d1d9")))
            if port.direction == Direction.OUTPUT:
                port_item.setPos(self.WIDTH, y)
                label.setPos(self.WIDTH - label.boundingRect().width() - 12.0, y - 9.0)
            else:
                port_item.setPos(0.0, y)
                label.setPos(12.0, y - 9.0)
            self.port_items[port.full_name] = port_item

        if node.parameters:
            self.setToolTip("Double-click to edit component parameters")
        self.setPos(*node.position)

    def itemChange(self, change, value):
        if (
            change == QtWidgets.QGraphicsItem.ItemPositionChange
            and self.node.locked
            and self.scene() is not None
        ):
            # Qt may move every selected item when one movable item is dragged.
            # Reject the proposed position so locked blocks also stay fixed
            # during rubber-band and multi-selection moves.
            return self.pos()
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            self.node.position = (float(value.x()), float(value.y()))
            self.editor.update_wires()
            if self.scene() is not None:
                self.editor.layout_changed.emit()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.node.parameters:
            self.editor.edit_parameters(self.node)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QtWidgets.QMenu()
        signals_action = menu.addAction("Manage block signals…")
        rename_action = menu.addAction("Rename block…")
        lock_action = menu.addAction(
            "Unlock block position" if self.node.locked else "Lock block position"
        )
        menu.addSeparator()
        remove_action = menu.addAction("Remove block")
        selected = menu.exec(event.screenPos()) if hasattr(menu, "exec") else menu.exec_(event.screenPos())
        if selected == signals_action:
            self.editor.focus_block_signals(self.node.node_id)
        elif selected == rename_action:
            self.editor.rename_node(self.node.node_id)
        elif selected == lock_action:
            self.editor.set_node_locked(self.node.node_id, not self.node.locked)
        elif selected == remove_action:
            self.editor.remove_node(self.node.node_id)
        event.accept()


class WireItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, signal_name: str, source: PortItem, destination: PortItem) -> None:
        super().__init__()
        self.signal_name = signal_name
        self.source = source
        self.destination = destination
        color = TYPE_COLORS[source.port.data_type]
        self.setPen(QtGui.QPen(color, 2.5))
        self.setZValue(0)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.setToolTip(signal_name)
        self.update_path()

    def update_path(self) -> None:
        start = self.source.center()
        end = self.destination.center()
        distance = max(60.0, abs(end.x() - start.x()) * 0.5)
        path = QtGui.QPainterPath(start)
        path.cubicTo(
            QtCore.QPointF(start.x() + distance, start.y()),
            QtCore.QPointF(end.x() - distance, end.y()),
            end,
        )
        self.setPath(path)

    def contextMenuEvent(self, event) -> None:
        menu = QtWidgets.QMenu()
        route_action = menu.addAction("Display as Goto/From")
        menu.addSeparator()
        disconnect_action = menu.addAction("Disconnect wire")
        selected = menu.exec(event.screenPos()) if hasattr(menu, "exec") else menu.exec_(event.screenPos())
        if selected == route_action:
            self.scene().set_signal_routed(self.signal_name, True)
        elif selected == disconnect_action:
            self.scene().disconnect_destination(
                self.signal_name, self.destination.port.full_name
            )
        event.accept()


class RouteTagItem(QtWidgets.QGraphicsRectItem):
    HEIGHT = 30.0

    def __init__(self, signal_name: str, kind: str, endpoint: PortItem) -> None:
        self.signal_name = signal_name
        self.kind = kind
        self.endpoint = endpoint
        width = max(110.0, min(230.0, 54.0 + len(signal_name) * 7.0))
        super().__init__(0.0, 0.0, width, self.HEIGHT)
        self.setBrush(QtGui.QBrush(QtGui.QColor("#21262d")))
        color = TYPE_COLORS[endpoint.port.data_type]
        self.setPen(QtGui.QPen(color, 2.0))
        self.setZValue(2)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        label = QtWidgets.QGraphicsSimpleTextItem(
            "%s  %s" % ("Goto" if kind == "goto" else "From", signal_name), self
        )
        label.setBrush(QtGui.QBrush(color))
        label.setPos(9.0, 7.0)
        self.setToolTip(
            "%s tag for HAL signal %s\nRight-click to restore direct wiring."
            % (kind.title(), signal_name)
        )
        self.update_position()

    def update_position(self) -> None:
        endpoint = self.endpoint.center()
        if self.kind == "goto":
            self.setPos(endpoint.x() + 55.0, endpoint.y() - self.HEIGHT / 2.0)
        else:
            self.setPos(
                endpoint.x() - self.rect().width() - 55.0,
                endpoint.y() - self.HEIGHT / 2.0,
            )

    def connection_point(self) -> QtCore.QPointF:
        rect = self.sceneBoundingRect()
        if self.kind == "goto":
            return QtCore.QPointF(rect.left(), rect.center().y())
        return QtCore.QPointF(rect.right(), rect.center().y())

    def contextMenuEvent(self, event) -> None:
        menu = QtWidgets.QMenu()
        signal = self.scene().project.signals.get(self.signal_name)
        direct_action = None
        if signal and signal.destinations:
            direct_action = menu.addAction("Display as direct wire")
        menu.addSeparator()
        delete_action = menu.addAction(
            "Delete Goto and HAL signal"
            if self.kind == "goto"
            else "Delete this From block"
        )
        selected = menu.exec(event.screenPos()) if hasattr(menu, "exec") else menu.exec_(event.screenPos())
        if direct_action is not None and selected == direct_action:
            self.scene().set_signal_routed(self.signal_name, False)
        elif selected == delete_action:
            if self.kind == "goto":
                self.scene().delete_signal(self.signal_name)
            else:
                self.scene().disconnect_destination(
                    self.signal_name, self.endpoint.port.full_name
                )
        event.accept()


class RouteStubItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, tag: RouteTagItem) -> None:
        super().__init__()
        self.tag = tag
        self.setPen(QtGui.QPen(TYPE_COLORS[tag.endpoint.port.data_type], 2.5))
        self.setZValue(0)
        self.update_path()

    def update_path(self) -> None:
        self.tag.update_position()
        endpoint = self.tag.endpoint.center()
        tag_point = self.tag.connection_point()
        path = QtGui.QPainterPath(endpoint if self.tag.kind == "goto" else tag_point)
        path.lineTo(tag_point if self.tag.kind == "goto" else endpoint)
        self.setPath(path)


class NoteItem(QtWidgets.QGraphicsRectItem):
    WIDTH = 210.0

    def __init__(self, note: Note, editor: "WiringScene") -> None:
        super().__init__()
        self.note = note
        self.editor = editor
        self.text_item = QtWidgets.QGraphicsTextItem(note.text, self)
        self.text_item.setDefaultTextColor(QtGui.QColor("#24292f"))
        self.text_item.setTextWidth(self.WIDTH - 18.0)
        font = self.text_item.font()
        font.setPointSize(9)
        self.text_item.setFont(font)
        self.text_item.setPos(9.0, 5.0)
        height = max(48.0, self.text_item.boundingRect().height() + 12.0)
        self.setRect(0.0, 0.0, self.WIDTH, height)
        self.setBrush(QtGui.QBrush(QtGui.QColor("#fff8c5")))
        self.setPen(QtGui.QPen(QtGui.QColor("#d4a72c"), 1.2))
        flags = (
            QtWidgets.QGraphicsItem.ItemIsSelectable
            | QtWidgets.QGraphicsItem.ItemSendsGeometryChanges
        )
        if not note.locked:
            flags |= QtWidgets.QGraphicsItem.ItemIsMovable
        self.setFlags(flags)
        self.setZValue(2)
        self.setToolTip("Visual note only; it is not included in HAL export")
        self.setPos(*note.position)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            self.note.position = (float(value.x()), float(value.y()))
            if self.scene() is not None:
                self.editor.layout_changed.emit()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event) -> None:
        self.editor.edit_note(self.note.note_id)
        event.accept()

    def contextMenuEvent(self, event) -> None:
        menu = QtWidgets.QMenu()
        edit_action = menu.addAction("Edit note…")
        delete_action = menu.addAction("Delete note")
        selected = (
            menu.exec(event.screenPos())
            if hasattr(menu, "exec")
            else menu.exec_(event.screenPos())
        )
        if selected == edit_action:
            self.editor.edit_note(self.note.note_id)
        elif selected == delete_action:
            self.editor.remove_note(self.note.note_id)
        event.accept()


class WiringScene(QtWidgets.QGraphicsScene):
    project_changed = QtCore.pyqtSignal() if QT_BINDING == "PyQt5" else QtCore.Signal()
    layout_changed = QtCore.pyqtSignal() if QT_BINDING == "PyQt5" else QtCore.Signal()

    def __init__(self, project: WiringProject, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.node_items: Dict[str, NodeItem] = {}
        self.port_items: Dict[str, PortItem] = {}
        self.wire_items = []
        self.route_tags = []
        self.route_stubs = []
        self.note_items: Dict[str, NoteItem] = {}
        self.connection_start: Optional[PortItem] = None
        self.preview_wire: Optional[QtWidgets.QGraphicsPathItem] = None
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        self.node_items.clear()
        self.port_items.clear()
        self.wire_items.clear()
        self.route_tags.clear()
        self.route_stubs.clear()
        self.note_items.clear()
        for node in self.project.nodes.values():
            if not node.active:
                continue
            item = NodeItem(node, self)
            self.addItem(item)
            self.node_items[node.node_id] = item
            self.port_items.update(item.port_items)
        for note in self.project.notes.values():
            item = NoteItem(note, self)
            self.addItem(item)
            self.note_items[note.note_id] = item
        self.rebuild_wires()
        self.setSceneRect(self.itemsBoundingRect().adjusted(-120, -120, 120, 120))

    def rebuild_wires(self) -> None:
        for item in self.wire_items + self.route_tags + self.route_stubs:
            self.removeItem(item)
        self.wire_items = []
        self.route_tags = []
        self.route_stubs = []
        for signal in self.project.signals.values():
            source = self.port_items.get(signal.source)
            if source is None:
                continue
            if signal.routed:
                goto_tag = RouteTagItem(signal.name, "goto", source)
                goto_stub = RouteStubItem(goto_tag)
                self.addItem(goto_stub)
                self.addItem(goto_tag)
                self.route_tags.append(goto_tag)
                self.route_stubs.append(goto_stub)
                for destination_name in signal.destinations:
                    destination = self.port_items.get(destination_name)
                    if destination is None:
                        continue
                    from_tag = RouteTagItem(signal.name, "from", destination)
                    from_stub = RouteStubItem(from_tag)
                    self.addItem(from_stub)
                    self.addItem(from_tag)
                    self.route_tags.append(from_tag)
                    self.route_stubs.append(from_stub)
                continue
            for destination_name in signal.destinations:
                destination = self.port_items.get(destination_name)
                if destination is None:
                    continue
                wire = WireItem(signal.name, source, destination)
                self.addItem(wire)
                self.wire_items.append(wire)

    def update_wires(self) -> None:
        for wire in self.wire_items:
            wire.update_path()
        for stub in self.route_stubs:
            stub.update_path()

    def begin_connection(self, port_item: PortItem) -> None:
        self.connection_start = port_item
        self.preview_wire = QtWidgets.QGraphicsPathItem()
        self.preview_wire.setPen(
            QtGui.QPen(TYPE_COLORS[port_item.port.data_type], 2.0, QtCore.Qt.DashLine)
        )
        self.preview_wire.setZValue(5)
        self.addItem(self.preview_wire)

    def update_connection(self, position: QtCore.QPointF) -> None:
        if not self.connection_start or not self.preview_wire:
            return
        start = self.connection_start.center()
        path = QtGui.QPainterPath(start)
        distance = max(60.0, abs(position.x() - start.x()) * 0.5)
        path.cubicTo(
            QtCore.QPointF(start.x() + distance, start.y()),
            QtCore.QPointF(position.x() - distance, position.y()),
            position,
        )
        self.preview_wire.setPath(path)

    def finish_connection(self, position: QtCore.QPointF) -> None:
        start = self.connection_start
        if self.preview_wire:
            self.removeItem(self.preview_wire)
        self.preview_wire = None
        self.connection_start = None
        if start is None:
            return
        target = next(
            (item for item in self.items(position) if isinstance(item, PortItem) and item is not start),
            None,
        )
        if target is None:
            return

        try:
            source_port, _ = self.project._orient(start.port, target.port)
            existing = self.project._signal_for_port(source_port.full_name)
            signal_name = None
            if existing is None or existing.source != source_port.full_name:
                default = self.project.suggest_signal_name(source_port)
                signal_name, accepted = QtWidgets.QInputDialog.getText(
                    self.views()[0], "HAL signal", "Signal name:", text=default
                )
                if not accepted:
                    return
                signal_name = str(signal_name).strip()
            self.project.connect(start.port.full_name, target.port.full_name, signal_name)
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self.views()[0], "Invalid connection", str(exc))
            return
        self.rebuild_wires()
        self.project_changed.emit()

    def edit_parameters(self, node: Node) -> None:
        dialog = QtWidgets.QDialog(self.views()[0])
        dialog.setWindowTitle("%s parameters" % node.node_id)
        dialog.resize(720, 650)
        layout = QtWidgets.QVBoxLayout(dialog)
        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Search parameters...")
        layout.addWidget(search)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(form_widget)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll)
        editors = {}
        for parameter in sorted(node.parameters.values(), key=lambda item: item.name):
            editor = QtWidgets.QLineEdit(parameter.value)
            editor.setToolTip(parameter.description)
            editor.setReadOnly(not parameter.writable)
            label = QtWidgets.QLabel(
                "%s  [%s %s]"
                % (
                    parameter.name,
                    parameter.data_type.value,
                    "RW" if parameter.writable else "RO",
                )
            )
            label.setToolTip(parameter.full_name)
            form.addRow(label, editor)
            editors[parameter.name] = (parameter, label, editor)

        def filter_parameters(text: str) -> None:
            search_text = text.strip().lower()
            for parameter, label, editor in editors.values():
                haystack = "%s %s %s" % (
                    parameter.name,
                    parameter.full_name,
                    parameter.description,
                )
                visible = not search_text or search_text in haystack.lower()
                label.setVisible(visible)
                editor.setVisible(visible)

        search.textChanged.connect(filter_parameters)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() if hasattr(dialog, "exec") else dialog.exec_():
            for name, (parameter, _label, editor) in editors.items():
                if parameter.writable:
                    node.parameters[name].value = editor.text().strip()
            self.project_changed.emit()

    def delete_selected_wires(self) -> None:
        connections = {
            (item.signal_name, item.destination.port.full_name)
            for item in self.selectedItems()
            if isinstance(item, WireItem)
        }
        for signal_name, destination_name in connections:
            self.project.disconnect_destination(signal_name, destination_name)
        if connections:
            self.rebuild_wires()
            self.project_changed.emit()

    def remove_node(self, node_id: str) -> None:
        self.project.deactivate_node(node_id)
        self.rebuild()
        self.project_changed.emit()

    def rename_node(self, node_id: str) -> None:
        node = self.project.nodes.get(node_id)
        if node is None:
            return
        new_id, accepted = QtWidgets.QInputDialog.getText(
            self.views()[0],
            "Rename HAL block",
            "New HAL block name:\n\nAll pins, parameters, and existing wiring "
            "endpoints will use this prefix.",
            text=node_id,
        )
        if not accepted:
            return
        try:
            renamed = self.project.rename_node(node_id, str(new_id))
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(
                self.views()[0], "Cannot rename block", str(exc)
            )
            return
        self.rebuild()
        self.project_changed.emit()
        self.focus_block_signals(renamed.node_id)

    def restore_node(self, node_id: str) -> None:
        self.project.activate_node(node_id)
        self.rebuild()
        self.project_changed.emit()

    def set_node_locked(self, node_id: str, locked: bool) -> None:
        try:
            self.project.set_node_locked(node_id, locked)
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(
                self.views()[0], "Cannot change block lock", str(exc)
            )
            return
        self.rebuild()
        self.project_changed.emit()
        self.focus_block_signals(node_id)

    def set_port_visible(self, node_id: str, port_name: str, visible: bool) -> None:
        self.project.set_port_visible(node_id, port_name, visible)
        self.rebuild()
        self.project_changed.emit()
        self.focus_block_signals(node_id)

    def create_goto(self, source_name: str) -> None:
        source = self.project.ports.get(source_name)
        if source is None:
            return
        default = self.project.suggest_signal_name(source)
        signal_name, accepted = QtWidgets.QInputDialog.getText(
            self.views()[0], "Create Goto signal", "HAL signal name:", text=default
        )
        if not accepted:
            return
        try:
            self.project.create_goto(source_name, str(signal_name).strip())
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self.views()[0], "Cannot create Goto", str(exc))
            return
        self.rebuild_wires()
        self.project_changed.emit()

    def connect_from_goto(self, destination_name: str) -> None:
        destination = self.project.ports.get(destination_name)
        if destination is None:
            return
        candidates = []
        for signal in self.project.signals.values():
            source = self.project.ports.get(signal.source)
            if (
                signal.routed
                and not signal.destinations
                and source is not None
                and source.data_type == destination.data_type
                and destination_name not in signal.destinations
            ):
                candidates.append(signal)
        if not candidates:
            QtWidgets.QMessageBox.information(
                self.views()[0],
                "No compatible Goto",
                "No visible Goto signal with type %s is available."
                % destination.data_type.value,
            )
            return
        labels = ["%s    (%s)" % (signal.name, signal.source) for signal in candidates]
        selected, accepted = QtWidgets.QInputDialog.getItem(
            self.views()[0],
            "Connect from Goto",
            "Goto signal:",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        signal = candidates[labels.index(str(selected))]
        try:
            self.project.connect_from(signal.name, destination_name)
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self.views()[0], "Cannot connect From", str(exc))
            return
        self.rebuild_wires()
        self.project_changed.emit()

    def set_signal_routed(self, signal_name: str, routed: bool) -> None:
        try:
            self.project.set_signal_routed(signal_name, routed)
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self.views()[0], "Cannot change routing", str(exc))
            return
        self.rebuild_wires()
        self.project_changed.emit()

    def delete_signal(self, signal_name: str) -> None:
        self.project.remove_signal(signal_name)
        self.rebuild_wires()
        self.project_changed.emit()

    def disconnect_destination(self, signal_name: str, destination_name: str) -> None:
        self.project.disconnect_destination(signal_name, destination_name)
        self.rebuild_wires()
        self.project_changed.emit()

    def add_note(self, position) -> None:
        text, accepted = QtWidgets.QInputDialog.getMultiLineText(
            self.views()[0],
            "Add visual note",
            "Note text (not included in HAL export):",
        )
        if not accepted or not str(text).strip():
            return
        self.project.add_note(
            str(text).strip(), (float(position.x()), float(position.y()))
        )
        self.rebuild()
        self.project_changed.emit()

    def edit_note(self, note_id: str) -> None:
        note = self.project.notes.get(note_id)
        if note is None:
            return
        text, accepted = QtWidgets.QInputDialog.getMultiLineText(
            self.views()[0], "Edit visual note", "Note text:", note.text
        )
        if not accepted:
            return
        if not str(text).strip():
            self.remove_note(note_id)
            return
        note.text = str(text).strip()
        self.rebuild()
        self.project_changed.emit()

    def remove_note(self, note_id: str) -> None:
        self.project.remove_note(note_id)
        self.rebuild()
        self.project_changed.emit()

    def contextMenuEvent(self, event) -> None:
        if self.items(event.scenePos()):
            super().contextMenuEvent(event)
            return
        menu = QtWidgets.QMenu()
        add_action = menu.addAction("Add visual note here…")
        selected = (
            menu.exec(event.screenPos())
            if hasattr(menu, "exec")
            else menu.exec_(event.screenPos())
        )
        if selected == add_action:
            self.add_note(event.scenePos())
        event.accept()

    def focus_block_signals(self, node_id: str) -> None:
        node_item = self.node_items.get(node_id)
        if node_item:
            self.clearSelection()
            node_item.setSelected(True)
        parent = self.parent()
        if parent is not None and hasattr(parent, "show_block_signals"):
            parent.show_block_signals(node_id)

    def delete_selected_items(self) -> None:
        selected = self.selectedItems()
        goto_signals = {
            item.signal_name
            for item in selected
            if isinstance(item, RouteTagItem) and item.kind == "goto"
        }
        connections = {
            (item.signal_name, item.destination.port.full_name)
            for item in selected
            if isinstance(item, WireItem)
        }
        connections.update(
            {
                (item.signal_name, item.endpoint.port.full_name)
                for item in selected
                if isinstance(item, RouteTagItem) and item.kind == "from"
            }
        )
        node_ids = {
            item.node.node_id for item in selected if isinstance(item, NodeItem)
        }
        note_ids = {
            item.note.note_id for item in selected if isinstance(item, NoteItem)
        }
        for signal_name in goto_signals:
            self.project.remove_signal(signal_name)
        for signal_name, destination_name in connections:
            self.project.disconnect_destination(signal_name, destination_name)
        for node_id in node_ids:
            self.project.deactivate_node(node_id)
        for note_id in note_ids:
            self.project.remove_note(note_id)
        if goto_signals or connections or node_ids or note_ids:
            self.rebuild()
            self.project_changed.emit()


class WiringView(QtWidgets.QGraphicsView):
    def __init__(self, scene: WiringScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(
            QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing
        )
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#0d1117")))

    def wheelEvent(self, event) -> None:
        if event.modifiers() & QtCore.Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key_Delete:
            self.scene().delete_selected_items()
            event.accept()
        elif event.key() == QtCore.Qt.Key_F2:
            node_item = next(
                (
                    item
                    for item in self.scene().selectedItems()
                    if isinstance(item, NodeItem)
                ),
                None,
            )
            if node_item:
                self.scene().rename_node(node_item.node.node_id)
                event.accept()
            else:
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)


class SignalDock(QtWidgets.QDockWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__("HAL signals", window)
        self.window = window
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(self.rename_selected)
        layout.addWidget(self.list)
        delete_button = QtWidgets.QPushButton("Delete signal")
        delete_button.clicked.connect(self.delete_selected)
        layout.addWidget(delete_button)
        self.setWidget(container)

    def refresh(self) -> None:
        self.list.clear()
        active = {signal.name: signal for signal in self.window.project.active_signals()}
        for signal in self.window.project.signals.values():
            active_signal = active.get(signal.name)
            if signal.routed and not signal.destinations:
                state = "  [Goto: waiting for From]"
            elif active_signal is None:
                state = "  [suspended]"
            elif len(active_signal.destinations) != len(signal.destinations):
                state = "  [partly suspended]"
            else:
                state = ""
            if signal.routed and signal.destinations:
                state += "  [Goto/From]"
            text = "%s\n  %s  ->  %s" % (
                signal.name + state,
                signal.source,
                ", ".join(signal.destinations),
            )
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, signal.name)
            self.list.addItem(item)

    def rename_selected(self, item=None) -> None:
        item = item or self.list.currentItem()
        if not item:
            return
        old_name = item.data(QtCore.Qt.UserRole)
        new_name, accepted = QtWidgets.QInputDialog.getText(
            self, "Rename signal", "Signal name:", text=old_name
        )
        if not accepted:
            return
        try:
            self.window.project.rename_signal(old_name, str(new_name).strip())
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid signal name", str(exc))
            return
        self.window.scene.rebuild_wires()
        self.window.project_modified()

    def delete_selected(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        self.window.project.remove_signal(item.data(QtCore.Qt.UserRole))
        self.window.scene.rebuild_wires()
        self.window.project_modified()


class AvailableBlocksDock(QtWidgets.QDockWidget):
    CATEGORIES = (
        (
            "joint",
            "LinuxCNC joints",
            "Motion commands, amplifier control, and position feedback.",
        ),
        (
            "cia402",
            "CiA 402 drive interface",
            "Drive state machine, operating mode, scaling, and homing.",
        ),
        (
            "ethercat",
            "EtherCAT / LCEC",
            "PDO signals exchanged with the physical EtherCAT slave.",
        ),
    )

    def __init__(self, window: "MainWindow") -> None:
        super().__init__("Block library", window)
        self.window = window
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        explanation = QtWidgets.QLabel(
            "Build the control path step by step:\n"
            "LinuxCNC joint  ->  CiA 402  ->  EtherCAT/LCEC\n\n"
            "Double-click an available block to add it."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Search blocks...")
        self.filter.textChanged.connect(self.refresh)
        layout.addWidget(self.filter)
        self.list = QtWidgets.QTreeWidget()
        self.list.setHeaderHidden(True)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self.restore_selected)
        layout.addWidget(self.list)
        add_button = QtWidgets.QPushButton("Add selected block")
        add_button.clicked.connect(self.restore_selected)
        layout.addWidget(add_button)
        self.setWidget(container)

    def refresh(self) -> None:
        self.list.clear()
        search = self.filter.text().strip().lower()
        categorized_ids = set()
        for kind, label, description in self.CATEGORIES:
            nodes = [
                node
                for node in self.window.project.nodes.values()
                if node.kind == kind
                and (
                    not search
                    or search in (node.node_id + " " + node.title + " " + description).lower()
                )
            ]
            if not nodes:
                continue
            categorized_ids.update(node.node_id for node in nodes)
            group = QtWidgets.QTreeWidgetItem(["%s (%d)" % (label, len(nodes))])
            group.setFlags(group.flags() & ~QtCore.Qt.ItemIsSelectable)
            group.setToolTip(0, description)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            group.setForeground(0, QtGui.QBrush(NODE_COLORS.get(kind, QtGui.QColor("#c9d1d9"))))
            self.list.addTopLevelItem(group)
            for node in sorted(nodes, key=lambda item: item.node_id):
                state = "on canvas" if node.active else "available"
                item = QtWidgets.QTreeWidgetItem(group, ["%s    [%s]" % (node.node_id, state)])
                item.setData(0, QtCore.Qt.UserRole, node.node_id)
                item.setToolTip(0, "%s\n%s" % (node.title, description))
                if node.active:
                    item.setForeground(0, QtGui.QBrush(QtGui.QColor("#8b949e")))

        other_nodes = [
            node
            for node in self.window.project.nodes.values()
            if node.node_id not in categorized_ids
            and (not search or search in (node.node_id + " " + node.title).lower())
        ]
        if other_nodes:
            group = QtWidgets.QTreeWidgetItem(["Other blocks (%d)" % len(other_nodes)])
            group.setFlags(group.flags() & ~QtCore.Qt.ItemIsSelectable)
            self.list.addTopLevelItem(group)
            for node in sorted(other_nodes, key=lambda item: item.node_id):
                state = "on canvas" if node.active else "available"
                item = QtWidgets.QTreeWidgetItem(group, ["%s    [%s]" % (node.node_id, state)])
                item.setData(0, QtCore.Qt.UserRole, node.node_id)
        self.list.expandAll()

    def restore_selected(self, item=None, column=0) -> None:
        if isinstance(item, QtWidgets.QTreeWidgetItem) and item.data(0, QtCore.Qt.UserRole):
            item.setSelected(True)
        items = self.list.selectedItems()
        if not items and self.list.currentItem():
            items = [self.list.currentItem()]
        node_ids = [item.data(0, QtCore.Qt.UserRole) for item in items]
        node_ids = [node_id for node_id in node_ids if node_id]
        if not node_ids:
            return
        changed = False
        for node_id in node_ids:
            node = self.window.project.nodes[node_id]
            if not node.active:
                self.window.project.activate_node(node_id)
                changed = True
        if changed:
            self.window.scene.rebuild()
            self.window.project_modified()
        self.window.scene.focus_block_signals(node_ids[-1])


class BlockSignalsDock(QtWidgets.QDockWidget):
    DIRECTION_GROUPS = (
        (Direction.INPUT, "Inputs"),
        (Direction.OUTPUT, "Outputs"),
        (Direction.IO, "Bidirectional"),
    )
    TYPE_ORDER = (
        DataType.BIT,
        DataType.FLOAT,
        DataType.S32,
        DataType.U32,
        DataType.S64,
        DataType.U64,
        DataType.UNKNOWN,
    )

    def __init__(self, window: "MainWindow") -> None:
        super().__init__("Block signals", window)
        self.window = window
        self.node_id: Optional[str] = None
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        self.block_label = QtWidgets.QLabel("Select a block on the canvas")
        self.block_label.setStyleSheet("font-weight: bold")
        layout.addWidget(self.block_label)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Search signals…")
        self.filter.textChanged.connect(self.refresh)
        layout.addWidget(self.filter)

        layout.addWidget(QtWidgets.QLabel("Available signals"))
        self.available = QtWidgets.QTreeWidget()
        self.available.setHeaderHidden(True)
        self.available.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.available.itemDoubleClicked.connect(self.add_selected)
        layout.addWidget(self.available)
        add_button = QtWidgets.QPushButton("Add selected >")
        add_button.clicked.connect(self.add_selected)
        layout.addWidget(add_button)

        layout.addWidget(QtWidgets.QLabel("Signals shown on block"))
        self.visible = QtWidgets.QTreeWidget()
        self.visible.setHeaderHidden(True)
        self.visible.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.visible.itemDoubleClicked.connect(self.remove_selected)
        layout.addWidget(self.visible)
        remove_button = QtWidgets.QPushButton("< Remove selected")
        remove_button.clicked.connect(self.remove_selected)
        layout.addWidget(remove_button)
        self.setWidget(container)

    def set_node(self, node_id: Optional[str]) -> None:
        self.node_id = node_id
        self.refresh()
        if node_id:
            self.raise_()

    def _signal_text(self, port: Port) -> str:
        connected = self.window.project._signal_for_port(port.full_name)
        suffix = "  [connected]" if connected else ""
        return "%s%s" % (port.name, suffix)

    @staticmethod
    def _group_item(text: str, parent=None):
        item = (
            QtWidgets.QTreeWidgetItem(parent, [text])
            if parent is not None
            else QtWidgets.QTreeWidgetItem([text])
        )
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        return item

    def _populate_tree(self, tree, ports) -> None:
        grouped = {}
        for port in ports:
            grouped.setdefault(port.direction, {}).setdefault(port.data_type, []).append(port)

        for direction, direction_label in self.DIRECTION_GROUPS:
            by_type = grouped.get(direction, {})
            direction_count = sum(len(items) for items in by_type.values())
            if not direction_count:
                continue
            direction_item = self._group_item(
                "%s (%d)" % (direction_label, direction_count)
            )
            tree.addTopLevelItem(direction_item)
            for data_type in self.TYPE_ORDER:
                typed_ports = sorted(by_type.get(data_type, []), key=lambda item: item.name)
                if not typed_ports:
                    continue
                type_item = self._group_item(
                    "%s (%d)" % (data_type.value, len(typed_ports)), direction_item
                )
                type_item.setForeground(0, QtGui.QBrush(TYPE_COLORS[data_type]))
                for port in typed_ports:
                    item = QtWidgets.QTreeWidgetItem(type_item, [self._signal_text(port)])
                    item.setForeground(0, QtGui.QBrush(TYPE_COLORS[data_type]))
                    item.setData(0, QtCore.Qt.UserRole, port.name)
                    item.setToolTip(0, "%s\n%s" % (port.full_name, port.description))
        tree.expandAll()

    def refresh(self) -> None:
        self.available.clear()
        self.visible.clear()
        node = self.window.project.nodes.get(self.node_id or "")
        if node is None or not node.active:
            self.block_label.setText("Select a block on the canvas")
            return
        self.block_label.setText(node.node_id)
        search = self.filter.text().strip().lower()
        available_ports = []
        visible_ports = []
        for port in node.ports.values():
            searchable = "%s %s %s" % (port.name, port.full_name, port.description)
            if search and search not in searchable.lower():
                continue
            (visible_ports if port.visible else available_ports).append(port)
        self._populate_tree(self.available, available_ports)
        self._populate_tree(self.visible, visible_ports)

    def _set_selected(self, source_list, visible: bool) -> None:
        if not self.node_id:
            return
        items = source_list.selectedItems()
        if not items and source_list.currentItem():
            items = [source_list.currentItem()]
        port_names = [item.data(0, QtCore.Qt.UserRole) for item in items]
        port_names = [name for name in port_names if name]
        if not port_names:
            return
        for port_name in port_names:
            self.window.project.set_port_visible(
                self.node_id, port_name, visible
            )
        self.window.scene.rebuild()
        self.window.project_modified()
        self.window.scene.focus_block_signals(self.node_id)
        self.refresh()

    def add_selected(self, item=None, column=0) -> None:
        if isinstance(item, QtWidgets.QTreeWidgetItem) and item.data(0, QtCore.Qt.UserRole):
            item.setSelected(True)
        self._set_selected(self.available, True)

    def remove_selected(self, item=None, column=0) -> None:
        if isinstance(item, QtWidgets.QTreeWidgetItem) and item.data(0, QtCore.Qt.UserRole):
            item.setSelected(True)
        self._set_selected(self.visible, False)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, project: WiringProject, comp_path: Path) -> None:
        super().__init__()
        self.project = project
        self.comp_path = Path(comp_path)
        self.project_path: Optional[Path] = None
        self.dirty = False
        self.setWindowTitle("CiA 402 HAL Wiring Editor")
        self.resize(1400, 900)
        self._set_project(project)
        self._create_actions()
        self._create_menus()
        self.statusBar().showMessage(
            self._startup_status()
        )

    def _startup_status(self) -> str:
        joint_count = sum(node.kind == "joint" for node in self.project.nodes.values())
        cia_count = sum(node.kind == "cia402" for node in self.project.nodes.values())
        if joint_count or cia_count:
            return (
                "Live HAL: %d joint block(s), %d cia402 block(s). Import EtherCAT XML to continue."
                % (joint_count, cia_count)
            )
        return "LinuxCNC HAL was not detected. Import EtherCAT XML to create an offline layout."

    def _set_project(self, project: WiringProject) -> None:
        self.project = project
        self.scene = WiringScene(project, self)
        self.scene.project_changed.connect(self.project_modified)
        self.scene.layout_changed.connect(self.layout_modified)
        self.view = WiringView(self.scene, self)
        self.setCentralWidget(self.view)
        if hasattr(self, "signal_dock"):
            self.removeDockWidget(self.signal_dock)
            self.signal_dock.deleteLater()
        if hasattr(self, "available_blocks_dock"):
            self.removeDockWidget(self.available_blocks_dock)
            self.available_blocks_dock.deleteLater()
        if hasattr(self, "block_signals_dock"):
            self.removeDockWidget(self.block_signals_dock)
            self.block_signals_dock.deleteLater()
        self.signal_dock = SignalDock(self)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.signal_dock)
        self.available_blocks_dock = AvailableBlocksDock(self)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.available_blocks_dock)
        self.block_signals_dock = BlockSignalsDock(self)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.block_signals_dock)
        self.splitDockWidget(
            self.signal_dock, self.block_signals_dock, QtCore.Qt.Vertical
        )
        self.signal_dock.refresh()
        self.available_blocks_dock.refresh()
        self.block_signals_dock.refresh()
        self.scene.selectionChanged.connect(self.canvas_selection_changed)

    def _create_actions(self) -> None:
        self.new_action = QAction("Import or reload EtherCAT XML…", self)
        self.new_action.setShortcut("Ctrl+N")
        self.new_action.triggered.connect(self.new_from_xml)
        self.refresh_action = QAction("Refresh live LinuxCNC HAL", self)
        self.refresh_action.setShortcut("F5")
        self.refresh_action.triggered.connect(self.refresh_live_hal)
        self.open_action = QAction("Open project…", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.open_project)
        self.save_action = QAction("Save project", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_project)
        self.save_as_action = QAction("Save project as…", self)
        self.save_as_action.triggered.connect(lambda: self.save_project(save_as=True))
        self.export_action = QAction("Export HAL…", self)
        self.export_action.setShortcut("Ctrl+E")
        self.export_action.triggered.connect(self.export_hal)
        self.preview_action = QAction("Preview HAL", self)
        self.preview_action.triggered.connect(self.preview_hal)
        self.validate_action = QAction("Validate wiring", self)
        self.validate_action.triggered.connect(self.validate_project)
        self.add_note_action = QAction("Add visual note…", self)
        self.add_note_action.setShortcut("Ctrl+Shift+N")
        self.add_note_action.triggered.connect(self.add_note_at_center)
        self.fit_action = QAction("Fit all blocks", self)
        self.fit_action.setShortcut("F")
        self.fit_action.triggered.connect(
            lambda: self.view.fitInView(self.scene.itemsBoundingRect(), QtCore.Qt.KeepAspectRatio)
        )

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.new_action)
        file_menu.addAction(self.refresh_action)
        file_menu.addAction(self.open_action)
        file_menu.addSeparator()
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(self.preview_action)
        file_menu.addAction(self.export_action)
        edit_menu = self.menuBar().addMenu("Wiring")
        edit_menu.addAction(self.validate_action)
        edit_menu.addAction(self.add_note_action)
        edit_menu.addAction(self.fit_action)

    def project_modified(self) -> None:
        self.dirty = True
        self.signal_dock.refresh()
        self.available_blocks_dock.refresh()
        self.block_signals_dock.refresh()
        if not self.windowTitle().endswith(" *"):
            self.setWindowTitle(self.windowTitle() + " *")

    def layout_modified(self) -> None:
        self.dirty = True
        if not self.windowTitle().endswith(" *"):
            self.setWindowTitle(self.windowTitle() + " *")

    def canvas_selection_changed(self) -> None:
        node_item = next(
            (item for item in self.scene.selectedItems() if isinstance(item, NodeItem)),
            None,
        )
        if node_item:
            self.show_block_signals(node_item.node.node_id)

    def show_block_signals(self, node_id: str) -> None:
        self.block_signals_dock.set_node(node_id)

    def add_note_at_center(self) -> None:
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_note(center)

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        result = QtWidgets.QMessageBox.question(
            self,
            "Unsaved project",
            "Discard the current unsaved changes?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        return result == QtWidgets.QMessageBox.Yes

    def new_from_xml(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open EtherCAT configuration", "", "EtherCAT XML (*.xml);;All files (*)"
        )
        if not filename:
            return
        try:
            incoming = build_project(Path(filename), self.comp_path)
            merge_project(self.project, incoming)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot load configuration", str(exc))
            return
        self.scene.rebuild()
        self.project_modified()
        self.statusBar().showMessage(
            "Merged %s. Existing and removed blocks were not duplicated." % filename, 8000
        )

    def refresh_live_hal(self) -> None:
        if not self._confirm_discard():
            return
        xml_path = self.project.source_files.get("ethercat_xml")
        try:
            if xml_path:
                project = build_project(Path(xml_path), self.comp_path)
            else:
                project = build_runtime_project(self.comp_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot read LinuxCNC HAL", str(exc))
            return
        self._set_project(project)
        self.project_path = None
        self.dirty = False
        self.setWindowTitle("CiA 402 HAL Wiring Editor")
        self.statusBar().showMessage(self._startup_status(), 8000)

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open wiring project", "", "CiA 402 project (*.json);;All files (*)"
        )
        if not filename:
            return
        try:
            project = load_project(Path(filename))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot open project", str(exc))
            return
        self._set_project(project)
        self.project_path = Path(filename)
        self.dirty = False
        self.setWindowTitle("CiA 402 HAL Wiring Editor — %s" % self.project_path.name)

    def save_project(self, save_as: bool = False) -> None:
        path = self.project_path
        if save_as or path is None:
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save wiring project", "cia402-wiring.json", "CiA 402 project (*.json)"
            )
            if not filename:
                return
            path = Path(filename)
        try:
            save_project(self.project, path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot save project", str(exc))
            return
        self.project_path = path
        self.dirty = False
        self.setWindowTitle("CiA 402 HAL Wiring Editor — %s" % path.name)
        self.statusBar().showMessage("Saved %s" % path, 5000)

    def validate_project(self) -> bool:
        errors = self.project.validate()
        if errors:
            QtWidgets.QMessageBox.warning(
                self, "Wiring errors", "\n".join("• " + error for error in errors)
            )
            return False
        QtWidgets.QMessageBox.information(self, "Wiring validation", "No wiring errors found.")
        return True

    def preview_hal(self) -> None:
        try:
            text = generate_hal(self.project)
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self, "Cannot generate HAL", str(exc))
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Generated HAL preview")
        dialog.resize(950, 650)
        layout = QtWidgets.QVBoxLayout(dialog)
        editor = QtWidgets.QPlainTextEdit(text)
        editor.setReadOnly(True)
        editor.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        layout.addWidget(editor)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()

    def export_hal(self) -> None:
        try:
            generate_hal(self.project)
        except WiringError as exc:
            QtWidgets.QMessageBox.warning(self, "Cannot generate HAL", str(exc))
            return
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export HAL wiring", "cia402-generated.hal", "LinuxCNC HAL (*.hal)"
        )
        if not filename:
            return
        try:
            write_hal(self.project, Path(filename))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot export HAL", str(exc))
            return
        self.statusBar().showMessage("Exported %s" % filename, 5000)

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def run(project: WiringProject, comp_path: Path) -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("CiA 402 HAL Wiring Editor")
    window = MainWindow(project, comp_path)
    window.show()
    return app.exec() if hasattr(app, "exec") else app.exec_()
