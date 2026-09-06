// Platform-native USB CDC-ACM backend for AccessoryLink on Device A.
// Binds the devicetree "usb-accessory" node (a usb-host-cdc class client
// beneath the shared ESP-IDF USB host installation) beneath the
// transport-independent AccessoryLinkService. AetherChat never touches
// USB host APIs directly.
#pragma once

#include <Tactility/service/ServiceManifest.h>

namespace tt::service::accessorylink {

extern const ServiceManifest usbCdcManifest;

} // namespace tt::service::accessorylink
