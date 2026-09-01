// Platform-native UART backend for AccessoryLink on Device A.
// Binds the devicetree "uart-accessory" controller (ESP32-P4 UART1,
// GPIO2 TX / GPIO3 RX, 921600 8N1) beneath the transport-independent
// AccessoryLinkService. AetherChat never touches UART/GPIO directly.
#pragma once

#include <Tactility/service/ServiceManifest.h>

namespace tt::service::accessorylink {

extern const ServiceManifest uartManifest;

} // namespace tt::service::accessorylink
