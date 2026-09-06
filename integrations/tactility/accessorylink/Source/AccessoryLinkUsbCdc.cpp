// Platform-native USB CDC-ACM backend for AccessoryLink on Device A.
//
// Layering: devicetree node "usb-accessory" (esp32_usbhost_cdc class
// client under the shared USB host service) -> this backend ->
// AccessoryLinkService (framing/negotiation) -> AetherChat.
//
// The same u32be-length + bounded-JSON protocol-v2 stream runs over USB,
// UART or any future transport; this file only moves bytes. Hotplug is
// physical here: connected() tracks the USB device, so the service's
// Unplugged/Discovering/Negotiating/Ready lifecycle maps directly onto
// cable/hub attach and detach events.
//
// The pump mirrors AccessoryLinkUart: it keeps the negotiation lifecycle
// alive while no app is subscribed, re-registering the backend after a
// rejected (silent or non-AetherLink) candidate so the next candidate or
// AetherChat's SESSION_OPEN edge always lands.
#ifdef ESP_PLATFORM

#include <Tactility/service/accessorylink/AccessoryLinkUsbCdc.h>
#include <Tactility/service/accessorylink/AccessoryLinkService.h>

#include <Tactility/service/Service.h>
#include <Tactility/service/ServiceRegistration.h>

#include <tactility/concurrent/thread.h>
#include <tactility/device.h>
#include <tactility/drivers/usb_host_cdc.h>
#include <tactility/freertos/task.h>
#include <tactility/log.h>

namespace tt::service::accessorylink {
namespace {

constexpr auto* TAG = "AccessoryLinkUsbCdc";
constexpr auto* USB_DEVICE_NAME = "usb-accessory";

Device* gUsb = nullptr;
Thread* gPump = nullptr;
volatile bool gPumpRunning = false;

int usbRead(void* context, uint8_t* data, size_t cap, uint32_t timeoutMs) {
    auto* device = static_cast<Device*>(context);
    return usb_host_cdc_read(device, data, cap, timeoutMs);
}

int usbWrite(void* context, const uint8_t* data, size_t length, uint32_t timeoutMs) {
    auto* device = static_cast<Device*>(context);
    return usb_host_cdc_write(device, data, length, timeoutMs);
}

bool usbOpen(void* context) {
    // The class client manages device lifetime asynchronously; the backend
    // is "open" as long as the devicetree device exists. connected()
    // reports actual accessory presence.
    return context != nullptr;
}

void usbClose(void*) {
    // No-op: the class client owns the CDC device handle.
}

bool usbConnected(void* context) {
    return usb_host_cdc_is_connected(static_cast<Device*>(context));
}

Capabilities usbCapabilities(void*) {
    return { .transport = "usb-cdc-acm-host", .flags = 0 };
}

void usbCancel(void*) {
    // RX reads carry their own timeout; nothing to unblock.
}

Backend makeBackend() {
    return Backend{
        .context = gUsb,
        .open = usbOpen,
        .close = usbClose,
        .read = usbRead,
        .write = usbWrite,
        .connected = usbConnected,
        .capabilities = usbCapabilities,
        .cancel = usbCancel,
    };
}

int32_t pumpMain(void*) {
    uint32_t idleCycles = 0;
    while (gPumpRunning) {
        const State s = state();
        if (s == State::Rejected || s == State::Error || s == State::Unplugged) {
            // Silent/wrong-candidate rejection with no app subscribed (or a
            // link drop): re-register so DISCOVERING -> NEGOTIATING re-arms
            // and the next onConnected edge reaches AetherChat. While the
            // cable is unplugged, re-arm only periodically.
            if (s != State::Unplugged || (idleCycles++ % 100) == 0) {
                unregisterPlatformBackend(gUsb);
                if (registerPlatformBackend(makeBackend())) {
                    LOG_I(TAG, "backend re-registered; awaiting accessory negotiation");
                }
            }
        }
        poll(10);
    }
    return 0;
}

class AccessoryLinkUsbCdcService : public Service {
public:
    bool onStart(ServiceContext&) override {
        if (device_get_by_name(USB_DEVICE_NAME, &gUsb) != ERROR_NONE || !gUsb) {
            LOG_E(TAG, "devicetree node '%s' not found; AetherLink USB unavailable",
                USB_DEVICE_NAME);
            return true; // service stays up; pump retries registration forever
        }
        if (!registerPlatformBackend(makeBackend())) {
            LOG_E(TAG, "%s backend registration failed", USB_DEVICE_NAME);
            return false;
        }
        LOG_I(TAG, "AetherLink USB CDC backend registered: %s", USB_DEVICE_NAME);
        gPumpRunning = true;
        // Tactility thread stack_size is BYTES; poll() alone carries a 1 KiB
        // RX buffer, and 3072 faulted (stack protection) under load.
        gPump = thread_alloc_full("accessorylink_usb", 8192, pumpMain, nullptr, tskNO_AFFINITY);
        if (!gPump || thread_start(gPump) != ERROR_NONE) {
            LOG_E(TAG, "pump thread start failed");
            gPumpRunning = false;
            unregisterPlatformBackend(gUsb);
            return false;
        }
        return true;
    }

    void onStop(ServiceContext&) override {
        gPumpRunning = false;
        if (gPump) {
            while (thread_get_state(gPump) != THREAD_STATE_STOPPED) vTaskDelay(pdMS_TO_TICKS(5));
            thread_free(gPump);
            gPump = nullptr;
        }
        unregisterPlatformBackend(gUsb);
    }
};

} // namespace

extern const ServiceManifest usbCdcManifest = {
    .id = "AccessoryLinkUsbCdc",
    .createService = create<AccessoryLinkUsbCdcService>,
};

} // namespace tt::service::accessorylink
#endif
