import pytest

from termiai.contracts import Reversibility, ToolResult
from termiai.tools import StubJournal, Tool, ToolContext, ToolRegistry, default_registry
from termiai.tools.edit import EditFile
from termiai.tools.files import ListFiles, MoveFile


class Dummy(Tool):
    name = "dummy"
    description = "d"
    parameters = {
        "text": {"type": "string"}, "n": {"type": "integer"}, "flag": {"type": "boolean"},
        "mode": {"type": "string", "enum": ["a", "b"]},
    }
    required = ("text",)

    def explain(self, args, ctx):
        return "does a dummy thing"

    def _run(self, args, ctx):
        if args.get("text") == "boom":
            raise PermissionError(13, "nope", "/x")
        return ToolResult(ok=True, output="ok")


def test_validation_messages(ctx):
    t = Dummy()
    assert "Missing" in t.validate({})
    assert "Unknown" in t.validate({"text": "x", "bogus": 1})
    assert "string" in t.validate({"text": 5})
    assert "integer" in t.validate({"text": "x", "n": True})  # bool is not an int
    assert "one of" in t.validate({"text": "x", "mode": "c"})
    assert t.validate({"text": "x", "n": 3, "flag": False, "mode": "a"}) is None


def test_run_never_raises_and_normalises_errors(ctx):
    t = Dummy()
    bad = t.run({}, ctx)
    assert not bad.ok and "Missing" in bad.error
    denied = t.run({"text": "boom"}, ctx)
    assert not denied.ok and "Permission denied" in denied.error
    assert t.run({"text": "fine"}, ctx).ok


def test_spec_is_valid_json_schema_shape():
    spec = Dummy().spec()
    fn = spec["function"]
    assert spec["type"] == "function" and fn["name"] == "dummy"
    assert fn["parameters"]["required"] == ["text"]
    assert fn["parameters"]["additionalProperties"] is False


def test_registry_rejects_duplicates_and_nameless():
    reg = ToolRegistry()
    reg.register(Dummy())
    with pytest.raises(ValueError):
        reg.register(Dummy())

    class Nameless(Dummy):
        name = ""

    with pytest.raises(ValueError):
        reg.register(Nameless())


def test_default_registry_has_every_planned_tool():
    names = set(default_registry().names())
    planned = {"list_files", "find_files", "read_file", "move_file", "copy_file", "create_folder", "delete_to_trash",
               "edit_file", "run_shell", "search_web", "open_url", "copy_to_clipboard", "get_system_info"}
    assert planned <= names  # every typed tool in PRD 6.2 plus edit_file/run_shell


def test_read_only_filter_hides_state_changing_tools():
    reg = default_registry()
    ro = {s["function"]["name"] for s in reg.specs(read_only_only=True)}
    for changing in ("move_file", "copy_file", "create_folder", "delete_to_trash", "edit_file", "run_shell",
                     "copy_to_clipboard", "open_url", "boot_kernel_once"):
        assert changing not in ro
    for diag in ("list_files", "find_files", "read_file", "read_logs", "read_device_info", "read_package_state",
                 "read_service_state", "search_web", "get_system_info", "list_kernels"):
        assert diag in ro


def test_every_tool_declares_explanation_and_reversibility(ctx):
    sample = {"list_files": {}, "find_files": {}, "read_file": {"path": "a"}, "move_file": {"source": "a", "destination": "b"},
              "copy_file": {"source": "a", "destination": "b"}, "create_folder": {"path": "a"}, "delete_to_trash": {"path": "a"},
              "edit_file": {"path": "a", "operation": "append", "content": "x"}, "run_shell": {"command": "ls", "explanation": "list"},
              "search_web": {"query": "q"}, "open_url": {"url": "https://x.y"}, "copy_to_clipboard": {"text": "t"},
              "get_system_info": {}, "read_logs": {}, "read_device_info": {"category": "cpu"}, "read_package_state": {"name": "x"},
              "read_service_state": {"name": "x"}, "list_kernels": {}, "boot_kernel_once": {}}
    reg = default_registry()
    assert set(sample) == set(reg.names()), "update this test when adding a tool"
    for name, args in sample.items():
        pv = reg.preview(name, args, ctx)
        assert pv.explanation.strip(), name
        assert isinstance(pv.reversibility, Reversibility), name


def test_read_only_tools_are_labelled_full(ctx):
    reg = default_registry()
    for tool in reg:
        if tool.read_only:
            assert tool.reversibility == Reversibility.FULL, tool.name


def test_preview_unknown_tool():
    with pytest.raises(KeyError):
        default_registry().preview("nope", {}, ToolContext())


def test_stub_journal_backs_up_and_records(tmp_path):
    j = StubJournal(backup_dir=tmp_path / "b")
    (tmp_path / "b").mkdir()
    f = tmp_path / "f.txt"
    f.write_text("hello")
    rec = j.backup_file(f)
    assert rec["sha256"] and (tmp_path / "b").exists()
    assert open(rec["backup"]).read() == "hello"
    assert j.backup_file(tmp_path / "missing") == {}


def test_context_resolves_relative_and_home(ctx, home):
    assert ctx.resolve("a/b") == home / "a" / "b"
    assert ctx.resolve("/abs") == __import__("pathlib").Path("/abs")
    assert str(ctx.resolve("~")) != "~"


def test_move_preview_labels_full_and_edit_preview_has_diff(ctx, home):
    (home / "a.txt").write_text("one\n")
    pv = MoveFile().preview({"source": "a.txt", "destination": "b.txt"}, ctx)
    assert pv.reversibility == Reversibility.FULL and "back" in pv.undo_hint.lower()
    pv = EditFile().preview({"path": "a.txt", "operation": "append", "content": "two\n"}, ctx)
    assert "+two" in pv.detail and not pv.read_only


def test_listfiles_preview_is_read_only(ctx):
    pv = ListFiles().preview({}, ctx)
    assert pv.read_only and pv.reversibility == Reversibility.FULL
