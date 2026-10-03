"""
Contract tests for the text adventure engine's public interfaces.

Covers:
  * Map contract      - rooms without exits, dangling exits, map validation
  * Item contract     - missing items, get/drop/examine edge cases
  * Command contract  - empty/whitespace/stop-word-only input, unknown commands
  * Save contract     - serialization round-trip, backward compatibility,
                        corrupted saves are recovered from or clearly rejected,
                        and a load can never trap the player

Run with:  python3 -m unittest test_text_adventure -v
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import text_adventure as ta

# The engine resolves game data through relative paths.
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def silence(func, *args, **kwargs):
    """Run func with typing delays disabled and stdout captured."""
    buf = io.StringIO()
    with mock.patch("time.sleep"), contextlib.redirect_stdout(buf):
        result = func(*args, **kwargs)
    return result, buf.getvalue()


def make_game():
    """Build a Game without the welcome screen or typing delays."""
    with mock.patch.object(ta, "display_welcome"):
        game, _ = silence(ta.Game)
    return game


def issue(game, line):
    """Issue a command line to the game (as cmdloop would), return output."""
    def drive():
        game.onecmd(game.precmd(line))
    _, out = silence(drive)
    return out


class TestMapContract(unittest.TestCase):
    """Map interface: Room, get_room, initialize_rooms, validate_map, move."""

    def test_room_without_exits_returns_none_for_every_direction(self):
        room = ta.Room(9, "Cell", "No way out.", {})
        for direction in (room.north, room.south, room.east,
                          room.west, room.up, room.down):
            self.assertIsNone(direction())

    def test_room_default_neighbors_are_not_shared(self):
        room_a = ta.Room()
        room_b = ta.Room()
        room_a.neighbors["n"] = 2
        self.assertEqual(room_b.neighbors, {})

    def test_get_room_returns_room_with_expected_data(self):
        rooms = ta.initialize_rooms()
        room = ta.get_room(1, rooms)
        self.assertEqual(room.id, 1)
        self.assertIsInstance(room, ta.Room)
        self.assertEqual(room.neighbors, rooms[1][2])

    def test_get_room_unknown_id_raises_keyerror(self):
        with self.assertRaises(KeyError):
            ta.get_room(999, ta.initialize_rooms())

    def test_validate_map_accepts_bundled_game(self):
        ta.validate_map(ta.initialize_rooms())

    def test_validate_map_rejects_dangling_exit(self):
        rooms = {1: ["A", "a", {"n": 2}]}
        with self.assertRaises(ValueError):
            ta.validate_map(rooms)

    def test_validate_map_rejects_unreachable_room(self):
        rooms = {1: ["A", "a", {}], 2: ["B", "b", {}]}
        with self.assertRaises(ValueError):
            ta.validate_map(rooms)

    def test_validate_map_rejects_missing_start_room(self):
        with self.assertRaises(ValueError):
            ta.validate_map({2: ["B", "b", {}]}, start_id=1)

    def test_move_into_missing_exit_stays_put(self):
        game = make_game()
        before = game.loc.id
        out = issue(game, "n")  # room 1 has no northern exit
        self.assertIn("can't go that way", out)
        self.assertEqual(game.loc.id, before)

    def test_move_along_dangling_exit_stays_put(self):
        game = make_game()
        game.loc = ta.Room(1, "R", "D", {"n": 999})
        _, out = silence(game.move, "n")
        self.assertIn("can't go that way", out)
        self.assertEqual(game.loc.id, 1)

    def test_engine_refuses_unwinnable_map_at_startup(self):
        broken = {"rooms": [
            {"id": 1, "name": "A", "description": "a", "neighbors": {}},
            {"id": 2, "name": "B", "description": "b", "neighbors": {}},
        ]}
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False) as fp:
            json.dump(broken, fp)
            path = fp.name
        try:
            with mock.patch.object(ta, "ROOM_FILE", path), \
                    mock.patch.object(ta, "display_welcome"), \
                    mock.patch("time.sleep"), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    ta.Game()
        finally:
            os.unlink(path)


class TestItemContract(unittest.TestCase):
    """Item interface: get / drop / examine / inventory."""

    def test_get_without_object_asks_for_specifics(self):
        out = issue(make_game(), "get")
        self.assertIn(ta.MORE_SPECIFIC_MSG, out)

    def test_get_nonexistent_item_reports_not_seen(self):
        out = issue(make_game(), "get xyzzy")
        self.assertIn("I don't see xyzzy here.", out)

    def test_get_item_in_other_room_reports_not_seen(self):
        game = make_game()  # mirror lives in room 3, player starts in room 1
        out = issue(game, "get mirror")
        self.assertIn("I don't see mirror here.", out)
        self.assertEqual(game.items["mirror"][2], 3)

    def test_get_character_reports_not_takeable(self):
        out = issue(make_game(), "get horse")
        self.assertIn("can't pick that up", out)

    def test_get_then_drop_roundtrip(self):
        game = make_game()
        issue(game, "e")
        issue(game, "u")  # room 5 holds the key
        self.assertEqual(game.loc.id, 5)
        out = issue(game, "get key")
        self.assertIn("You got the key.", out)
        self.assertEqual(game.items["key"][2], 0)
        out = issue(game, "get key")
        self.assertIn("already have the key", out)
        out = issue(game, "inv")
        self.assertIn("interdimensional key", out)
        out = issue(game, "drop key")
        self.assertIn("You dropped the key.", out)
        self.assertEqual(game.items["key"][2], 5)

    def test_drop_nonexistent_item(self):
        out = issue(make_game(), "drop xyzzy")
        self.assertIn("You don't have xyzzy.", out)

    def test_drop_item_not_held(self):
        out = issue(make_game(), "drop mirror")
        self.assertIn("You don't have mirror.", out)

    def test_examine_nonexistent(self):
        out = issue(make_game(), "examine xyzzy")
        self.assertIn("can't examine that here", out)

    def test_examine_without_object_asks_for_specifics(self):
        out = issue(make_game(), "examine")
        self.assertIn(ta.MORE_SPECIFIC_MSG, out)

    def test_unlock_without_key_reports_missing_tool(self):
        game = make_game()
        for step in ("w", "d"):  # room 4, the portal room
            issue(game, step)
        out = issue(game, "unlock portal")
        self.assertIn("don't have anything to unlock", out)
        self.assertEqual(game.flags["portal_unlocked"], "False")

    def test_unlock_with_key_sets_flag(self):
        game = make_game()
        for step in ("e", "u", "get key", "d", "w", "w", "d"):
            issue(game, step)
        self.assertEqual(game.loc.id, 4)
        out = issue(game, "unlock portal")
        self.assertIn("You unlocked the portal.", out)
        self.assertEqual(game.flags["portal_unlocked"], "True")
        out = issue(game, "unlock portal")
        self.assertIn("already unlocked", out)


class TestCommandContract(unittest.TestCase):
    """Command interface: parsing, empty input, unknown commands."""

    def test_parse_empty_input(self):
        self.assertEqual(ta.parse_input(""), "")

    def test_parse_whitespace_only_input(self):
        self.assertEqual(ta.parse_input("   "), "")

    def test_parse_stop_words_only_input(self):
        ta.load_stop_words()
        self.assertEqual(ta.parse_input("the a of"), "")

    def test_parse_expands_synonyms(self):
        ta.load_stop_words()
        self.assertEqual(ta.parse_input("x lamp"), "examine lamp")
        self.assertEqual(ta.parse_input("go north"), "go n")

    def test_split_input_empty(self):
        self.assertEqual(ta.split_input(""), "")

    def test_empty_command_does_not_crash(self):
        out = issue(make_game(), "")
        self.assertIn(ta.EMPTYLINE_MSG, out)

    def test_whitespace_command_does_not_crash(self):
        out = issue(make_game(), "    ")
        self.assertIn(ta.EMPTYLINE_MSG, out)

    def test_stop_word_only_command_does_not_crash(self):
        out = issue(make_game(), "the a of")
        self.assertIn(ta.EMPTYLINE_MSG, out)

    def test_unknown_command_reports_unknown(self):
        out = issue(make_game(), "xyzzy foobar")
        self.assertIn(ta.UNKNOWN_MSG, out)


class TestSaveContract(unittest.TestCase):
    """Save/load contract: round-trip, compatibility, corruption handling."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "save.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_save(self, data):
        with open(self.path, "w") as fp:
            if isinstance(data, str):
                fp.write(data)
            else:
                json.dump(data, fp)

    def test_save_writes_versioned_json(self):
        game = make_game()
        game.save_game(self.path)
        with open(self.path) as fp:
            data = json.load(fp)
        self.assertEqual(data["version"], ta.SAVE_VERSION)
        self.assertEqual(data["location"], 1)
        self.assertIn("items", data)
        self.assertIn("flags", data)

    def test_roundtrip_preserves_state(self):
        game = make_game()
        for step in ("e", "u", "get key", "d", "w", "w", "d", "unlock portal"):
            issue(game, step)
        game.save_game(self.path)

        restored = make_game()
        restored.load_game(self.path)
        self.assertEqual(restored.loc.id, game.loc.id)
        self.assertEqual(restored.items, game.items)
        self.assertEqual(restored.characters, game.characters)
        self.assertEqual(restored.flags, game.flags)

    def test_roundtrip_is_stable(self):
        game = make_game()
        issue(game, "e")
        game.save_game(self.path)
        restored = make_game()
        restored.load_game(self.path)
        self.assertEqual(restored.serialize(), game.serialize())

    def test_legacy_unversioned_save_still_loads(self):
        legacy = {
            "location": 3,
            "items": {"mirror": 0, "key": 5},
            "flags": {"portal_unlocked": "False", "portal_entered": "False"},
        }
        self.write_save(legacy)
        game = make_game()
        game.load_game(self.path)
        self.assertEqual(game.loc.id, 3)
        self.assertEqual(game.items["mirror"][2], 0)
        self.assertEqual(game.items["key"][2], 5)

    def test_corrupted_json_is_rejected(self):
        self.write_save("{ not valid json !!!")
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_missing_save_file_is_rejected(self):
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(os.path.join(self.tmp.name, "nope.json"))

    def test_save_missing_fields_is_rejected(self):
        self.write_save({"version": ta.SAVE_VERSION})
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_newer_save_version_is_rejected(self):
        self.write_save({"version": ta.SAVE_VERSION + 1, "location": 1,
                         "items": {}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_save_with_unknown_room_is_rejected(self):
        self.write_save({"location": 999, "items": {}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_save_with_unknown_item_is_rejected(self):
        self.write_save({"location": 1, "items": {"xyzzy": 0}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_save_with_item_in_unknown_room_is_rejected(self):
        self.write_save({"location": 1, "items": {"key": 999}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_save_with_unknown_flag_is_rejected(self):
        self.write_save({"location": 1, "items": {},
                         "flags": {"xyzzy": "True"}})
        with self.assertRaises(ta.SaveGameError):
            make_game().load_game(self.path)

    def test_failed_load_leaves_current_state_untouched(self):
        game = make_game()
        issue(game, "e")
        self.write_save({"location": 999, "items": {}, "flags": {}})
        with self.assertRaises(ta.SaveGameError):
            game.load_game(self.path)
        self.assertEqual(game.loc.id, 3)

    def test_load_never_traps_player(self):
        # A valid save in any room must reload into a fully connected map.
        for room_id in ta.initialize_rooms():
            game = make_game()
            game.loc = ta.get_room(room_id, game.rooms)
            game.save_game(self.path)
            restored = make_game()
            restored.load_game(self.path)
            self.assertEqual(restored.loc.id, room_id)
            ta.validate_map(restored.rooms, start_id=restored.loc.id)


if __name__ == "__main__":
    unittest.main()
