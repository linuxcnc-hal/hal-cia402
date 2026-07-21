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
from .model import DataType, Direction, Node, Port, WiringError, WiringProject
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


def build_runtime_project(comp_path: Path) -> WiringProject:
    """Create the initial canvas from a running LinuxCNC HAL."""

    project = WiringProject()
    project.source_files = {"component": str(Path(comp_path).resolve())}
    live_nodes, live_parameters = discover_live_hal()
    attach_component_parameters(live_nodes, comp_path, live_parameters)
    vertical_spacing = max((len(node.ports) for node in live_nodes), default=1) * 22.0 + 100.0
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
    if components:
        attach_component_parameters(components, comp_path, live_parameters)
    else:
        inferred_instance_count = instance_count or max(1, len(joints), len(ethercat_nodes))
        components = parse_comp(comp_path, inferred_instance_count)

    all_nodes = joints + components + ethercat_nodes
    vertical_spacing = max((len(node.ports) for node in all_nodes), default=1) * 22.0 + 100.0
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


class NodeItem(QtWidgets.QGraphicsRectItem):
    WIDTH = 310.0
    HEADER = 30.0
    ROW = 22.0

    def __init__(self, node: Node, editor: "WiringScene") -> None:
        height = self.HEADER + max(1, len(node.ports)) * self.ROW + 10.0
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

        for row, port in enumerate(node.ports.values()):
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
        remove_action = menu.addAction("Remove block")
        selected = menu.exec(event.screenPos()) if hasattr(menu, "exec") else menu.exec_(event.screenPos())
        if selected == remove_action:
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


class WiringScene(QtWidgets.QGraphicsScene):
    project_changed = QtCore.pyqtSignal() if QT_BINDING == "PyQt5" else QtCore.Signal()
    layout_changed = QtCore.pyqtSignal() if QT_BINDING == "PyQt5" else QtCore.Signal()

    def __init__(self, project: WiringProject, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.node_items: Dict[str, NodeItem] = {}
        self.port_items: Dict[str, PortItem] = {}
        self.wire_items = []
        self.connection_start: Optional[PortItem] = None
        self.preview_wire: Optional[QtWidgets.QGraphicsPathItem] = None
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        self.node_items.clear()
        self.port_items.clear()
        self.wire_items.clear()
        for node in self.project.nodes.values():
            if not node.active:
                continue
            item = NodeItem(node, self)
            self.addItem(item)
            self.node_items[node.node_id] = item
            self.port_items.update(item.port_items)
        self.rebuild_wires()
        self.setSceneRect(self.itemsBoundingRect().adjusted(-120, -120, 120, 120))

    def rebuild_wires(self) -> None:
        for wire in self.wire_items:
            self.removeItem(wire)
        self.wire_items = []
        for signal in self.project.active_signals():
            source = self.port_items.get(signal.source)
            if source is None:
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
        layout = QtWidgets.QFormLayout(dialog)
        editors = {}
        for parameter in node.parameters.values():
            editor = QtWidgets.QLineEdit(parameter.value)
            editor.setToolTip(parameter.description)
            layout.addRow(parameter.name, editor)
            editors[parameter.name] = editor
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() if hasattr(dialog, "exec") else dialog.exec_():
            for name, editor in editors.items():
                node.parameters[name].value = editor.text().strip()
            self.project_changed.emit()

    def delete_selected_wires(self) -> None:
        names = {
            item.signal_name for item in self.selectedItems() if isinstance(item, WireItem)
        }
        for name in names:
            self.project.remove_signal(name)
        if names:
            self.rebuild_wires()
            self.project_changed.emit()

    def remove_node(self, node_id: str) -> None:
        self.project.deactivate_node(node_id)
        self.rebuild()
        self.project_changed.emit()

    def restore_node(self, node_id: str) -> None:
        self.project.activate_node(node_id)
        self.rebuild()
        self.project_changed.emit()

    def delete_selected_items(self) -> None:
        signal_names = {
            item.signal_name for item in self.selectedItems() if isinstance(item, WireItem)
        }
        node_ids = {
            item.node.node_id for item in self.selectedItems() if isinstance(item, NodeItem)
        }
        for name in signal_names:
            self.project.remove_signal(name)
        for node_id in node_ids:
            self.project.deactivate_node(node_id)
        if signal_names or node_ids:
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
            if active_signal is None:
                state = "  [suspended]"
            elif len(active_signal.destinations) != len(signal.destinations):
                state = "  [partly suspended]"
            else:
                state = ""
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
    def __init__(self, window: "MainWindow") -> None:
        super().__init__("Available blocks", window)
        self.window = window
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        explanation = QtWidgets.QLabel(
            "Removed blocks remain available here.\nDouble-click a block to restore it."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.list = QtWidgets.QListWidget()
        self.list.itemDoubleClicked.connect(self.restore_selected)
        layout.addWidget(self.list)
        add_button = QtWidgets.QPushButton("Add block")
        add_button.clicked.connect(self.restore_selected)
        layout.addWidget(add_button)
        self.setWidget(container)

    def refresh(self) -> None:
        self.list.clear()
        for node in self.window.project.nodes.values():
            if node.active:
                continue
            item = QtWidgets.QListWidgetItem("%s    [%s]" % (node.node_id, node.kind))
            item.setData(QtCore.Qt.UserRole, node.node_id)
            self.list.addItem(item)

    def restore_selected(self, item=None) -> None:
        if not isinstance(item, QtWidgets.QListWidgetItem):
            item = self.list.currentItem()
        if not item:
            return
        self.window.scene.restore_node(item.data(QtCore.Qt.UserRole))

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
        self.signal_dock = SignalDock(self)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.signal_dock)
        self.available_blocks_dock = AvailableBlocksDock(self)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.available_blocks_dock)
        self.signal_dock.refresh()
        self.available_blocks_dock.refresh()

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
        edit_menu.addAction(self.fit_action)

    def project_modified(self) -> None:
        self.dirty = True
        self.signal_dock.refresh()
        self.available_blocks_dock.refresh()
        if not self.windowTitle().endswith(" *"):
            self.setWindowTitle(self.windowTitle() + " *")

    def layout_modified(self) -> None:
        self.dirty = True
        if not self.windowTitle().endswith(" *"):
            self.setWindowTitle(self.windowTitle() + " *")

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
