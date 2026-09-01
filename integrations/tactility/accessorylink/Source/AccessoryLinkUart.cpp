// Platform-native UART backend for AccessoryLink on Device A (ESP32-P4).
//
// Layering: Tactility UART controller (devicetree node "uart-accessory",
// UART1, GPIO2 TX / GPIO3 RX, 921600 8N1, no flow control) -> this backend
// -> AccessoryLinkService (framing/negotiation) -> AetherChat.
//
// The same u32be-length + bounded-JSON protocol-v2 stream runs over USB,
// UART or any future transport; this file only moves bytes.
//
// The pump keeps the negotiation lifecycle alive while no app is subscribed:
// AccessoryLinkService rejects a silent candidate after its negotiation
// timeout, so the backend re-registers until AetherChat subscribes and its
// SESSION_OPEN (sent from onConnected) reaches Device B.
#ifdef ESP_PLATFORM

#include <Tactility/service/accessorylink/AccessoryLinkUart.h>
#include <Tactility/service/accessorylink/AccessoryLinkService.h>

#include <Tactility/service/Service.h>
#include <Tactility/service/ServiceRegistration.h>

#include <tactility/concurrent/thread.h>
#include <tactility/device.h>
#include <tactility/drivers/uart_controller.h>
#include <tactility/freertos/task.h>
#include <tactility/log.h>

#include <algorithm>

namespace tt::service::accessorylink {
namespace {

constexpr auto* TAG = "AccessoryLinkUart";
constexpr auto* UART_DEVICE_NAME = "uart-accessory";
constexpr uint32_t UART_BAUD = 921600;

Device* gUart = nullptr;
Thread* gPump = nullptr;
volatile bool gPumpRunning = false;

int uartRead(void* context, uint8_t* data, size_t cap, uint32_t timeoutMs) {
    auto* device = static_cast<Device*>(context);
    // Block up to timeoutMs for the first byte, then drain what is buffered.
    if (uart_controller_read_byte(device, data, pdMS_TO_TICKS(timeoutMs)) != ERROR_NONE) {
        return 0;
    }
    size_t total = 1;
    size_t available = 0;
    if (total < cap &&
        uart_controller_get_available(device, &available) == ERROR_NONE && available > 0) {
        const size_t want = std::min(available, cap - total);
        if (uart_controller_read_bytes(device, data + total, want, pdMS_TO_TICKS(20)) == ERROR_NONE) {
            total += want;
        }
    }
    return static_cast<int>(total);
}

int uartWrite(void* context, const uint8_t* data, size_t length, uint32_t timeoutMs) {
    auto* device = static_cast<Device*>(context);
    return uart_controller_write_bytes(device, data, length, pdMS_TO_TICKS(timeoutMs)) == ERROR_NONE
        ? static_cast<int>(length) : 0;
}

bool uartOpen(void* context) {
    return uart_controller_open(static_cast<Device*>(context)) == ERROR_NONE;
}

void uartClose(void* context) {
    auto* device = static_cast<Device*>(context);
    if (uart_controller_is_open(device)) uart_controller_close(device);
}

bool uartConnected(void* context) {
    return uart_controller_is_open(static_cast<Device*>(context));
}

Capabilities uartCapabilities(void*) {
    return { .transport = "uart-921600-8N1", .flags = 0 };
}

void uartCancel(void* context) {
    uart_controller_flush_input(static_cast<Device*>(context));
}

Backend makeBackend() {
    return Backend{
        .context = gUart,
        .open = uartOpen,
        .close = uartClose,
        .read = uartRead,
        .write = uartWrite,
        .connected = uartConnected,
        .capabilities = uartCapabilities,
        .cancel = uartCancel,
    };
}

int32_t pumpMain(void*) {
    uint32_t idleCycles = 0;
    while (gPumpRunning) {
        const State s = state();
        if (s == State::Rejected || s == State::Error || s == State::Unplugged) {
            // Silent-candidate timeout with no app subscribed (or a link
            // drop): re-register so DISCOVERING -> NEGOTIATING re-arms and
            // the next onConnected edge reaches AetherChat.
            if (s != State::Unplugged || (idleCycles++ % 100) == 0) {
                unregisterPlatformBackend(gUart);
                if (registerPlatformBackend(makeBackend())) {
                    LOG_I(TAG, "backend re-registered; awaiting AetherChat negotiation");
                }
            }
        }
        poll(10);
    }
    return 0;
}

class AccessoryLinkUartService : public Service {
public:
    bool onStart(ServiceContext&) override {
        if (device_get_by_name(UART_DEVICE_NAME, &gUart) != ERROR_NONE || !gUart) {
            LOG_E(TAG, "devicetree node '%s' not found; AetherLink UART unavailable",
                UART_DEVICE_NAME);
            return true; // service stays up; pump retries registration forever
        }
        const UartConfig config = {
            .baud_rate = UART_BAUD,
            .data_bits = UART_CONTROLLER_DATA_8_BITS,
            .parity = UART_CONTROLLER_PARITY_DISABLE,
            .stop_bits = UART_CONTROLLER_STOP_BITS_1,
        };
        if (uart_controller_set_config(gUart, &config) != ERROR_NONE) {
            LOG_E(TAG, "%s rejected 921600 8N1 config", UART_DEVICE_NAME);
            return false;
        }
        if (!registerPlatformBackend(makeBackend())) {
            LOG_E(TAG, "%s open failed", UART_DEVICE_NAME);
            return false;
        }
        LOG_I(TAG, "AetherLink UART backend open: %s (UART1, TX GPIO2, RX GPIO3, 921600 8N1)",
            UART_DEVICE_NAME);
        gPumpRunning = true;
        gPump = thread_alloc_full("accessorylink_uart", 3072, pumpMain, nullptr, tskNO_AFFINITY);
        if (!gPump || thread_start(gPump) != ERROR_NONE) {
            LOG_E(TAG, "pump thread start failed");
            gPumpRunning = false;
            unregisterPlatformBackend(gUart);
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
        unregisterPlatformBackend(gUart);
    }
};

} // namespace

extern const ServiceManifest uartManifest = {
    .id = "AccessoryLinkUart",
    .createService = create<AccessoryLinkUartService>,
};

} // namespace tt::service::accessorylink
#endif
