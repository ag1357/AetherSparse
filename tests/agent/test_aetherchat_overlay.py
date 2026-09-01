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
    input.sessionId = 7;
    input.requestId = 9;
    input.sequence = 11;
    input.payload = "Who was Alan Turing?";
    std::vector<std::vector<uint8_t>> wire;
    assert(encodeMessage(input, wire));
    assert(wire.size() == 1);
    Message output;
    Reassembler reassembler;
    assert(reassembler.feed(wire[0].data(), wire[0].size(), output));
    assert(output.payload == input.payload && output.requestId == 9);
    wire[0][4] ^= 1;
    assert(!reassembler.feed(wire[0].data(), wire[0].size(), output));
    const auto json = buildEnvelopeJson(
        MessageType::UserText, 7, 9, 11, "\"text\":\"Who was Alan Turing?\""
    );
    assert(json.find("\"protocol_version\":\"aethercore-tactility.v2\"") != std::string::npos);
    assert(json.find("\"type\":\"USER_TEXT\"") != std::string::npos);
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
    assert len(cpp_files) == 4  # app/view/protocol plus service consumer
    cpp_loc = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in cpp_files)
    assert cpp_loc <= 850
    combined = "\n".join(path.read_text(encoding="utf-8") for path in cpp_files)
    assert "listen(fd, 1)" not in combined
    assert "service::webserver::isWebServerEnabled()" not in combined
    assert "service::accessorylink::subscribe" in combined
    assert "service::wifi::connect" not in combined
    assert "esp_wifi_" not in combined
    assert "lvgl_hardware_keyboard_is_available" in combined
