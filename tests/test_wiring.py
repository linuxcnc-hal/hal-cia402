from pathlib import Path
import tempfile
import unittest

from cia402_gui.generator import generate_hal
from cia402_gui.model import DataType, Direction, Node, Port, WiringError, WiringProject
from cia402_gui.parsers import (
    _nodes_from_pin_info,
    attach_component_parameters,
    create_joint_nodes,
    parse_comp,
    parse_ethercat_xml,
)
from cia402_gui.project_io import load_project, save_project


ROOT = Path(__file__).resolve().parent.parent


class ParserTests(unittest.TestCase):
    def test_component_pins_and_parameters_are_discovered(self):
        node = parse_comp(ROOT / "cia402.comp")[0]

        self.assertEqual(node.ports["pos-cmd"].direction, Direction.INPUT)
        self.assertEqual(node.ports["pos-cmd"].data_type, DataType.FLOAT)
        self.assertEqual(node.ports["controlword"].direction, Direction.OUTPUT)
        self.assertEqual(node.ports["controlword"].data_type, DataType.U32)
        self.assertEqual(node.parameters["pos-scale"].value, "1.0")
        self.assertEqual(node.parameters["csp-mode"].value, "1")

    def test_multiple_component_instances_get_unique_names(self):
        nodes = parse_comp(ROOT / "cia402.comp", instance_count=2)

        self.assertEqual(nodes[1].node_id, "cia402.1")
        self.assertEqual(nodes[1].ports["pos-cmd"].full_name, "cia402.1.pos-cmd")

    def test_ethercat_direction_is_converted_to_hal_direction(self):
        node = parse_ethercat_xml(ROOT / "example" / "ethercat-conf.xml")[0]

        self.assertEqual(node.ports["cia-statusword"].direction, Direction.OUTPUT)
        self.assertEqual(node.ports["cia-statusword"].data_type, DataType.U32)
        self.assertEqual(node.ports["cia-controlword"].direction, Direction.INPUT)
        self.assertEqual(node.ports["actual-position"].data_type, DataType.S32)

    def test_joint_template_has_motion_interface_pins(self):
        node = create_joint_nodes(1)[0]

        self.assertEqual(node.ports["motor-pos-cmd"].direction, Direction.OUTPUT)
        self.assertEqual(node.ports["motor-pos-fb"].direction, Direction.INPUT)
        self.assertEqual(node.ports["index-enable"].direction, Direction.IO)

    def test_live_hal_pin_information_creates_real_joint_and_cia_nodes(self):
        nodes = _nodes_from_pin_info(
            [
                {
                    "NAME": "joint.0.motor-pos-cmd",
                    "TYPE": "float",
                    "DIRECTION": "OUT",
                },
                {
                    "NAME": "joint.0.motor-pos-fb",
                    "TYPE": "float",
                    "DIRECTION": "IN",
                },
                {
                    "NAME": "cia402.0.pos-cmd",
                    "TYPE": "float",
                    "DIRECTION": "IN",
                },
                {
                    "NAME": "unrelated.pin",
                    "TYPE": "bit",
                    "DIRECTION": "OUT",
                },
            ]
        )

        by_id = {node.node_id: node for node in nodes}
        self.assertEqual(set(by_id), {"joint.0", "cia402.0"})
        self.assertEqual(
            by_id["joint.0"].ports["motor-pos-cmd"].direction,
            Direction.OUTPUT,
        )
        self.assertEqual(
            by_id["cia402.0"].ports["pos-cmd"].direction,
            Direction.INPUT,
        )

    def test_live_component_parameters_replace_source_defaults(self):
        nodes = _nodes_from_pin_info(
            [
                {
                    "NAME": "cia402.0.pos-cmd",
                    "TYPE": "float",
                    "DIRECTION": "IN",
                }
            ]
        )
        attach_component_parameters(
            nodes,
            ROOT / "cia402.comp",
            {"cia402.0.pos-scale": (DataType.FLOAT, "10000000.0")},
        )

        self.assertEqual(nodes[0].parameters["pos-scale"].value, "10000000.0")


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.project = WiringProject()
        self.joint = create_joint_nodes(1)[0]
        self.component = parse_comp(ROOT / "cia402.comp")[0]
        self.ethercat = parse_ethercat_xml(ROOT / "example" / "ethercat-conf.xml")[0]
        for node in (self.joint, self.component, self.ethercat):
            self.project.add_node(node)

    def test_connections_generate_canonical_hal(self):
        self.project.connect(
            "joint.0.motor-pos-cmd", "cia402.0.pos-cmd", "x-pos-cmd"
        )
        self.project.connect(
            "cia402.0.pos-fb", "joint.0.motor-pos-fb", "x-pos-fb"
        )
        self.project.connect(
            "lcec.0.0.cia-statusword", "cia402.0.statusword", "x-statusword"
        )
        self.project.connect(
            "cia402.0.controlword", "lcec.0.0.cia-controlword", "x-controlword"
        )

        output = generate_hal(self.project)

        self.assertIn(
            "net x-pos-cmd", output
        )
        self.assertIn(
            "joint.0.motor-pos-cmd", output
        )
        self.assertIn(
            "=> cia402.0.pos-cmd", output
        )
        self.assertIn(
            "net x-pos-fb", output
        )
        self.assertIn(
            "cia402.0.pos-fb", output
        )
        self.assertIn(
            "=> joint.0.motor-pos-fb", output
        )
        self.assertIn("setp cia402.0.pos-scale", output)

    def test_dragging_from_input_to_output_is_oriented_automatically(self):
        signal = self.project.connect(
            "cia402.0.pos-cmd", "joint.0.motor-pos-cmd", "x-pos-cmd"
        )

        self.assertEqual(signal.source, "joint.0.motor-pos-cmd")
        self.assertEqual(signal.destinations, ["cia402.0.pos-cmd"])

    def test_output_to_output_is_rejected(self):
        with self.assertRaisesRegex(WiringError, "two output"):
            self.project.connect(
                "joint.0.motor-pos-cmd", "cia402.0.pos-fb", "bad-signal"
            )

    def test_type_mismatch_is_rejected(self):
        with self.assertRaisesRegex(WiringError, "Type mismatch"):
            self.project.connect(
                "joint.0.amp-enable-out", "cia402.0.pos-cmd", "bad-signal"
            )

    def test_an_input_can_have_only_one_signal(self):
        self.project.connect(
            "joint.0.motor-pos-cmd", "cia402.0.pos-cmd", "x-pos-cmd"
        )
        with self.assertRaisesRegex(WiringError, "already connected"):
            self.project.connect(
                "cia402.0.velocity-fb", "cia402.0.pos-cmd", "second-writer"
            )

    def test_one_output_can_feed_multiple_inputs(self):
        signal = self.project.connect(
            "joint.0.amp-enable-out", "cia402.0.enable", "x-enable"
        )
        # Add a second compatible input solely to exercise HAL fan-out.
        node = Node(node_id="consumer", title="consumer", kind="test")
        node.ports["enable"] = Port(
            name="enable",
            full_name="consumer.enable",
            direction=Direction.INPUT,
            data_type=DataType.BIT,
        )
        self.project.add_node(node)
        same_signal = self.project.connect(
            "joint.0.amp-enable-out", "consumer.enable"
        )

        self.assertIs(signal, same_signal)
        self.assertEqual(len(signal.destinations), 2)
        output = generate_hal(self.project)
        self.assertIn("=> cia402.0.enable \\", output)
        self.assertIn("=> consumer.enable", output)

    def test_project_round_trip_preserves_wiring_and_parameters(self):
        self.project.connect(
            "joint.0.motor-pos-cmd", "cia402.0.pos-cmd", "x-pos-cmd"
        )
        self.component.parameters["pos-scale"].value = "10000000"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            save_project(self.project, path)
            restored = load_project(path)

        self.assertEqual(restored.signals["x-pos-cmd"].source, "joint.0.motor-pos-cmd")
        self.assertEqual(
            restored.nodes["cia402.0"].parameters["pos-scale"].value,
            "10000000",
        )

    def test_invalid_parameter_text_cannot_be_injected_into_hal(self):
        self.component.parameters["pos-scale"].value = "1\nnet injected"

        with self.assertRaisesRegex(WiringError, "Invalid parameter value"):
            generate_hal(self.project)


if __name__ == "__main__":
    unittest.main()
