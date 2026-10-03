"""
Contract tests for the text adventure engine's public interfaces.

These tests treat the map, item, command and save/load interfaces of
text_adventure.py as unbreakable contracts:

* Map interface: rooms without exits, dangling exits and unknown room ids
  must never crash the engine or strand the player.
* Item interface: referencing items that do not exist (or are not here)
  must produce feedback, never an exception or silence.
* Command interface: empty and unknown commands must be handled gracefully.
* Save interface: state must survive a save/load round-trip, corrupted
  saves must be explicitly rejected without clobbering live state, the
  save format must stay backward compatible, and loading must never put
  the player in a room they can never leave (because it does not exist).

Run with: python3 -m unittest test_text_adventure -v
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import text_adventure as ta


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


class GameTestCase(unittest.TestCase):
    """Base class: builds a Game with screen output captured, not printed."""

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        self.output = []
        self._patchers = [
            mock.patch.object(ta, "display_output", self._capture),
            mock.patch.object(ta, "display_welcome", lambda: None),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.game = ta.Game()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        os.chdir(self._cwd)

    def _capture(self, text, type=""):
        self.output.append((type, text))

    def messages(self):
        return [text for _, text in self.output]

    def last_message(self):
        self.assertTrue(self.output, "expected the game to say something")
        return self.output[-1][1]


class TestRoomContract(GameTestCase):
    """Map interface: exits, missing exits, unknown rooms."""

    def test_room_without_exits_returns_none_in_all_directions(self):
        room = ta.Room(id=99, name="Nowhere", description="A void.")
        for direction in ("north", "south", "east", "west", "up", "down"):
            self.assertIsNone(getattr(room, direction)())

    def test_room_default_neighbors_are_not_shared(self):
        first = ta.Room()
        second = ta.Room()
        first.neighbors["n"] = 5
        self.assertEqual(second.neighbors, {})

    def test_get_room_with_unknown_id_raises_clear_error(self):
        with self.assertRaises(KeyError):
            ta.get_room(999, self.game.rooms)

    def test_move_into_wall_keeps_player_in_place(self):
        self.game.loc = ta.get_room(4, self.game.rooms)  # basement: only "u"
        self.game.move("n")
        self.assertEqual(self.game.loc.id, 4)
        self.assertIn("can't go that way", self.last_message())

    def test_dangling_exit_never_strands_player(self):
        # A map authoring error: room 1 points north at a room that
        # does not exist. Moving there must not crash or teleport the
        # player into the void.
        self.game.rooms[1][2]["n"] = 999
        self.game.loc = ta.get_room(1, self.game.rooms)
        self.game.move("n")
        self.assertEqual(self.game.loc.id, 1)
        self.assertIn(self.game.loc.id, self.game.rooms)


class TestItemContract(GameTestCase):
    """Item interface: nonexistent, absent and empty item references."""

    def test_get_nonexistent_item_gives_feedback(self):
        self.game.do_get("nonexistent")
        self.assertIn("nonexistent", self.last_message())

    def test_get_item_in_other_room_is_not_silent(self):
        # The mirror is in room 3; the player starts in room 1.
        self.game.do_get("mirror")
        self.assertIn("mirror", self.last_message())
        self.assertNotEqual(self.game.items["mirror"][2], 0)

    def test_get_without_argument_asks_for_specifics(self):
        self.game.do_get("")
        self.assertEqual(self.last_message(), ta.MORE_SPECIFIC_MSG)

    def test_drop_nonexistent_item_gives_feedback(self):
        self.game.do_drop("nonexistent")
        self.assertIn("nonexistent", self.last_message())

    def test_examine_nonexistent_item_gives_feedback(self):
        self.game.do_examine("nonexistent")
        self.assertTrue(self.last_message())

    def test_talk_to_nonexistent_character_gives_feedback(self):
        self.game.do_talk("nonexistent")
        self.assertIn("nonexistent", self.last_message())

    def test_get_then_drop_roundtrip_updates_location(self):
        self.game.loc = ta.get_room(3, self.game.rooms)
        self.game.do_get("mirror")
        self.assertEqual(self.game.items["mirror"][2], 0)
        self.game.do_drop("mirror")
        self.assertEqual(self.game.items["mirror"][2], 3)


class TestCommandContract(GameTestCase):
    """Command interface: empty, blank and unknown commands."""

    def test_parse_empty_input_returns_empty_command(self):
        self.assertEqual(ta.parse_input(""), "")

    def test_parse_only_stop_words_returns_empty_command(self):
        self.assertEqual(ta.parse_input("the a of"), "")

    def test_parse_applies_synonyms(self):
        self.assertEqual(ta.parse_input("west"), "w")
        self.assertEqual(ta.parse_input("get key"), "get key")

    def test_emptyline_is_handled(self):
        self.game.emptyline()
        self.assertEqual(self.last_message(), ta.EMPTYLINE_MSG)

    def test_onecmd_with_empty_line_is_handled(self):
        self.game.onecmd("")
        self.assertEqual(self.last_message(), ta.EMPTYLINE_MSG)

    def test_unknown_command_is_handled(self):
        self.game.onecmd("xyzzy plugh")
        self.assertEqual(self.last_message(), ta.UNKNOWN_MSG)


class TestSaveLoadContract(GameTestCase):
    """Save interface: round-trip, corruption, compatibility, safety."""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.save_path = os.path.join(self._tmpdir.name, "save.json")

    def tearDown(self):
        self._tmpdir.cleanup()
        super().tearDown()

    def _write_save_file(self, payload):
        with open(self.save_path, "w") as fp:
            if isinstance(payload, str):
                fp.write(payload)
            else:
                json.dump(payload, fp)

    def test_save_load_roundtrip_preserves_state(self):
        game = self.game
        game.loc = ta.get_room(3, game.rooms)
        game.do_get("mirror")
        game.flags["portal_unlocked"] = "True"
        game.save_game(self.save_path)

        # Scramble live state, then reload.
        game.loc = ta.get_room(1, game.rooms)
        game.items["mirror"][2] = 5
        game.flags["portal_unlocked"] = "False"
        game.load_game(self.save_path)

        self.assertEqual(game.loc.id, 3)
        self.assertEqual(game.items["mirror"][2], 0)
        self.assertEqual(game.flags["portal_unlocked"], "True")

    def test_save_file_is_versioned_json(self):
        self.game.save_game(self.save_path)
        with open(self.save_path) as fp:
            payload = json.load(fp)
        self.assertIn("version", payload)
        self.assertEqual(payload["loc"], self.game.loc.id)
        self.assertIn("items", payload)
        self.assertIn("flags", payload)

    def test_corrupted_save_is_explicitly_rejected(self):
        self._write_save_file("{ this is not json !!!")
        with self.assertRaises(ta.SaveGameError):
            self.game.load_game(self.save_path)
        # Live state must be untouched.
        self.assertEqual(self.game.loc.id, 1)

    def test_save_with_wrong_shape_is_rejected(self):
        for bad_payload in ([1, 2, 3], "just a string", 42, {"rooms": {}}):
            self._write_save_file(bad_payload)
            with self.assertRaises(ta.SaveGameError):
                self.game.load_game(self.save_path)
        self.assertEqual(self.game.loc.id, 1)

    def test_save_with_unknown_room_is_rejected_and_not_applied(self):
        self._write_save_file({"version": 1, "loc": 999,
                               "items": {}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            self.game.load_game(self.save_path)
        # The player must remain in a real room, never the void.
        self.assertIn(self.game.loc.id, self.game.rooms)

    def test_save_with_item_in_nonexistent_room_is_rejected(self):
        self._write_save_file({"version": 1, "loc": 1,
                               "items": {"key": 999}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            self.game.load_game(self.save_path)

    def test_legacy_save_without_version_still_loads(self):
        # Backward compatibility: saves written before the format was
        # versioned must keep working.
        self._write_save_file({"loc": 3,
                               "items": {"mirror": 0},
                               "flags": {"portal_unlocked": "True"}})
        self.game.load_game(self.save_path)
        self.assertEqual(self.game.loc.id, 3)
        self.assertEqual(self.game.items["mirror"][2], 0)
        self.assertEqual(self.game.flags["portal_unlocked"], "True")

    def test_save_with_unknown_top_level_fields_is_tolerated(self):
        # Forward compatibility: newer saves may add fields.
        self._write_save_file({"version": 99, "loc": 2, "future_field": 1,
                               "items": {}, "flags": {}})
        self.game.load_game(self.save_path)
        self.assertEqual(self.game.loc.id, 2)

    def test_missing_save_file_raises_os_error(self):
        with self.assertRaises(OSError):
            self.game.load_game(self.save_path)

    def test_save_and_load_commands(self):
        self.game.loc = ta.get_room(2, self.game.rooms)
        self.game.onecmd("save " + self.save_path)
        self.assertTrue(os.path.exists(self.save_path))
        self.game.loc = ta.get_room(1, self.game.rooms)
        self.game.onecmd("load " + self.save_path)
        self.assertEqual(self.game.loc.id, 2)

    def test_load_command_with_corrupted_save_reports_failure(self):
        self._write_save_file("garbage")
        self.game.onecmd("load " + self.save_path)
        self.assertEqual(self.game.loc.id, 1)
        self.assertTrue(self.last_message())


if __name__ == "__main__":
    unittest.main()
