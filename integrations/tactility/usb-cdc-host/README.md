# Device-A USB CDC-ACM host integration (AetherLink production transport)

Overlay for the custom Device-A Tactility tree (v0.8.0-dev,
`Devices/waveshare-esp32-p4-wifi6-touch-lcd-35`). Files here mirror their
target paths inside the Tactility tree and are synced byte-identical.

Contents:

- `Platforms/platform-esp32/source/drivers/usb/esp32_usbhost_cdc.cpp` —
  CDC-ACM class client driver (compatible `espressif,esp32-usbhost-cdc`)
  wrapping Espressif `usb_host_cdc_acm` 2.1.1 beneath Tactility's shared
  `usb_host_install` (alongside HID/MSC/MIDI). RX callback -> stream
  buffer; blocking read/write API. VID/PID are discovery hints only.
- `Platforms/platform-esp32/include/tactility/drivers/esp32_usbhost_cdc.h` —
  devicetree config (vid/pid).
- `Platforms/platform-esp32/include/tactility/bindings/esp32_usbhost.h` —
  adds `DEFINE_DEVICETREE(esp32_usbhost_cdc, ...)`.
- `Platforms/platform-esp32/bindings/espressif,esp32-usbhost-cdc.yaml` —
  binding (vid/pid int properties).
- `TactilityKernel/include/tactility/drivers/usb_host_cdc.h` +
  `TactilityKernel/source/drivers/usb_host_cdc.cpp` — kernel device type
  `USB_HOST_CDC_TYPE` + API wrappers (same pattern as usb_host_midi).

Manual edit points (not expressible as whole-file overlay):

1. `Platforms/platform-esp32/source/module.cpp`: extern +
   `driver_construct_add(&esp32_usbhost_cdc_driver)` under
   `SOC_USB_OTG_SUPPORTED`, and matching `driver_remove_destruct` (before
   the usbhost parent).
2. `Platforms/platform-esp32/CMakeLists.txt`: add
   `espressif__usb_host_cdc_acm` to `idf_component_optional_requires`.
3. `Firmware/idf_component.yml`: add `espressif/usb_host_cdc_acm: "~2.1.0"`
   with `target in [esp32s3, esp32p4]` rule.
4. Board dts: `#include <tactility/bindings/esp32_usbhost.h>` plus

   ```
   usbhost0 {
       compatible = "espressif,esp32-usbhost";
       peripheral-map = <0>;   // USB OTG Type-C on USBD_P/N pads
       usb_accessory: usb-accessory {
           compatible = "espressif,esp32-usbhost-cdc";
           vid = <0x303A>;
           pid = <0x4001>;
       };
   };
   ```

   `hardware.usbHostEnabled=true` in `device.properties` already emits
   `CONFIG_USB_HOST_HUBS_SUPPORTED=y` for the powered external hub.
5. `Tactility/Source/Tactility.cpp`: register
   `service::accessorylink::usbCdcManifest` when a `usb-accessory` node
   exists, else the UART fallback (`uartManifest`). Only one backend may
   own AccessoryLinkService's single platform slot.

The AccessoryLink USB backend service itself lives in
`integrations/tactility/accessorylink/` (`AccessoryLinkUsbCdc.{h,cpp}`).

Bench status (work/aethercore-v15-final-integration): builds green with
ESP-IDF v5.5.2; Device-A boot log shows usb host lib start
(peripheral_map=0x00), `esp32_usbhost_cdc` client task up, and
`AccessoryLinkUsbCdc` registered. Physical hub/Device-B enumeration is the
next gate.
