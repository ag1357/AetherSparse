from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "integrations" / "tactility" / "aetherchat"


def test_aetherlink_protocol_is_cxx17_buildable_and_rejects_forgery(tmp_path: Path) -> None:
    harness = tmp_path / "aetherlink_test.cpp"
    harness.write_text(
        r'''
#include <Tactility/app/aetherchat/AetherLinkProtocol.h>
#include <cassert>
#include <vector>
using namespace tt::app::aetherchat;
int main() {
    Message input;
    input.type = MessageType::UserText;
    input.final = true;
    input.sessionId = 7;
    input.requestId = 9;
    input.sequence = 11;
    input.payload = "Who was Alan Turing?";
    std::vector<uint8_t> wire;
    assert(serializeMessage(input, wire));
    Message output;
    assert(deserializeMessage(wire.data(), wire.size(), output));
    assert(output.payload == input.payload && output.requestId == 9 && output.final);
    wire[4] = 99;
    assert(!deserializeMessage(wire.data(), wire.size(), output));
    return 0;
}
''',
        encoding="utf-8",
    )
    binary = tmp_path / "aetherlink_test"
    subprocess.run(
        (
            "c++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{OVERLAY / 'Private'}",
            str(OVERLAY / "Source/app/aetherchat/AetherLinkProtocol.cpp"),
            str(harness),
            "-o",
            str(binary),
        ),
        check=True,
    )
    subprocess.run((str(binary),), check=True)


def test_aetherchat_remains_below_upstream_chat_complexity_control() -> None:
    cpp_files = tuple(sorted((OVERLAY / "Source/app/aetherchat").glob("*.cpp")))
    assert len(cpp_files) == 3  # upstream Chat: 5 C++ files
    cpp_loc = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in cpp_files)
    assert cpp_loc <= 992  # exact upstream Chat C++ LOC at 0ee2415
    combined = "\n".join(path.read_text(encoding="utf-8") for path in cpp_files)
    assert "tt::service::espnow" not in combined  # namespace is used as service::espnow in tt
    assert "service::espnow::send" in combined
    assert "window_manager_create" in combined
    assert "lvgl_hardware_keyboard_is_available" in combined
