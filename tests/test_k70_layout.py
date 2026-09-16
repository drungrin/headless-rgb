from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
HEADER_PATH = REPO_ROOT / "mac-agent" / "k70max_layout.h"


def _load_generator():
    # The generator is build tooling, not part of the installed package, so it
    # is loaded by path instead of imported from headless_lights.
    path = REPO_ROOT / "tools" / "gen_k70_layout.py"
    spec = importlib.util.spec_from_file_location("gen_k70_layout", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


class ParseHeaderTests(unittest.TestCase):
    def test_parses_every_hardware_channel(self) -> None:
        entries = gen.parse_header(HEADER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(entries), gen.WIRE_SLOTS)

    def test_counts_the_physically_lit_channels(self) -> None:
        entries = gen.parse_header(HEADER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(sum(1 for _, _, mapped in entries if mapped), 116)

    def test_rejects_a_header_without_the_table(self) -> None:
        with self.assertRaises(gen.LayoutError):
            gen.parse_header("int main() { return 0; }")

    def test_rejects_a_truncated_table(self) -> None:
        text = "kK70LedCoordinates{{ {0.0, 0.0, true}, {1.0, 1.0, true}, }};"
        with self.assertRaises(gen.LayoutError):
            gen.parse_header(text)


class BuildLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = gen.parse_header(HEADER_PATH.read_text(encoding="utf-8"))
        self.names, self.positions, self.wire_index, self.size = gen.build_layout(
            self.entries
        )

    def test_one_entry_per_lit_channel(self) -> None:
        self.assertEqual(len(self.names), 116)
        self.assertEqual(len(self.positions), 116)
        self.assertEqual(len(self.wire_index), 116)

    def test_every_cell_is_distinct(self) -> None:
        cells = [tuple(position) for position in self.positions]
        self.assertEqual(len(set(cells)), len(cells))

    def test_every_cell_is_inside_the_grid(self) -> None:
        width, height = self.size
        for column, row in self.positions:
            self.assertGreaterEqual(column, 0)
            self.assertGreaterEqual(row, 0)
            self.assertLess(column, width)
            self.assertLess(row, height)

    def test_wire_index_matches_the_mapped_channels(self) -> None:
        expected = [
            index for index, (_, _, mapped) in enumerate(self.entries) if mapped
        ]
        self.assertEqual(self.wire_index, expected)

    def test_wire_index_is_ascending_and_within_the_frame(self) -> None:
        self.assertEqual(self.wire_index, sorted(self.wire_index))
        self.assertLess(max(self.wire_index), gen.WIRE_SLOTS)

    def test_names_line_up_with_positions(self) -> None:
        for name, (column, row) in zip(self.names, self.positions):
            self.assertEqual(name, f"R{row}C{column}")

    def test_rows_preserve_left_to_right_order(self) -> None:
        mapped = [
            (index, x, y)
            for index, (x, y, is_mapped) in enumerate(self.entries)
            if is_mapped
        ]
        cell_of = dict(zip(self.wire_index, self.positions))
        by_row: dict[int, list[tuple[float, int]]] = {}
        for index, x, _ in mapped:
            column, row = cell_of[index]
            by_row.setdefault(row, []).append((x, column))
        for row, members in by_row.items():
            members.sort()
            columns = [column for _, column in members]
            self.assertEqual(columns, sorted(columns), f"row {row} is out of order")

    def test_generation_is_deterministic(self) -> None:
        again = gen.build_layout(self.entries)
        self.assertEqual(again, (self.names, self.positions, self.wire_index, self.size))

    def test_raises_when_a_row_cannot_fit(self) -> None:
        crowded = [(index / 100.0, 0.0, True) for index in range(gen.WIRE_SLOTS)]
        with self.assertRaises(gen.LayoutError):
            gen.build_layout(crowded, width=4)


class RenderBlockTests(unittest.TestCase):
    def test_block_is_delimited_by_the_splice_markers(self) -> None:
        entries = gen.parse_header(HEADER_PATH.read_text(encoding="utf-8"))
        block = gen.render_block(*gen.build_layout(entries))
        self.assertTrue(block.startswith(gen.BEGIN_MARKER))
        self.assertTrue(block.endswith(gen.END_MARKER))

    def test_splice_replaces_only_the_generated_region(self) -> None:
        plugin = f"before\n{gen.BEGIN_MARKER}\nold\n{gen.END_MARKER}\nafter\n"
        spliced = gen.splice(plugin, f"{gen.BEGIN_MARKER}\nnew\n{gen.END_MARKER}")
        self.assertEqual(
            spliced, f"before\n{gen.BEGIN_MARKER}\nnew\n{gen.END_MARKER}\nafter\n"
        )

    def test_splice_rejects_a_plugin_without_markers(self) -> None:
        with self.assertRaises(gen.LayoutError):
            gen.splice("no markers here", "block")


if __name__ == "__main__":
    unittest.main()
