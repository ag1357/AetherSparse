// USB CDC-ACM host class client for the AetherLink accessory link.
//
// One additional class client beneath Tactility's shared ESP-IDF USB host
// installation (see esp32_usbhost.cpp), alongside the existing HID/MSC/MIDI
// clients. Wraps Espressif's usb_host_cdc_acm component: the component owns
// its client task; this driver owns one accessory open/close lifecycle and
// exposes a blocking byte-stream API (kernel type "usb-host-cdc") consumed
// by the AccessoryLink USB backend service.
//
// RX is callback-driven in usb_host_cdc_acm 2.x; the data callback copies
// into a stream buffer so read() can block with a timeout. VID/PID from the
// devicetree are discovery hints only; protocol-v2 negotiation above this
// driver is authoritative for accessory identity.
#include <sdkconfig.h>
#ifdef CONFIG_SOC_USB_OTG_SUPPORTED

#include <tactility/device.h>
#include <tactility/driver.h>
#include <tactility/drivers/esp32_usbhost_cdc.h>
#include <tactility/drivers/usb_host_cdc.h>
#include <tactility/log.h>

#include <atomic>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <freertos/stream_buffer.h>

#include <usb/cdc_acm_host.h>
#include <usb/cdc_acm_host_ops.h>

#define TAG "esp32_usbhost_cdc"

#define GET_CONFIG(device) ((const Esp32UsbHostCdcConfig*)(device)->config)

constexpr auto CDC_TASK_STACK        = 4096;
constexpr auto CDC_TASK_PRIORITY     = 5;
constexpr auto CDC_STOP_TIMEOUT_MS   = 3000;
constexpr auto CDC_OPEN_RETRY_MS     = 1000;
constexpr auto CDC_RX_STREAM_SIZE    = 4096;
constexpr auto CDC_IN_BUFFER_SIZE    = 4096;
constexpr auto CDC_OUT_BUFFER_SIZE   = 2048;

struct UsbCdcContext {
    cdc_acm_dev_hdl_t    dev         = nullptr;
    std::atomic<bool>    connected   {false};
    std::atomic<bool>    running     {false};
    std::atomic<bool>    gone        {false};
    TaskHandle_t         task_handle = nullptr;
    SemaphoreHandle_t    task_done   = nullptr;
    StreamBufferHandle_t rx_stream   = nullptr;
    uint16_t             vid         = 0;
    uint16_t             pid         = 0;
};

static bool cdc_data_cb(const uint8_t* data, size_t data_len, void* arg) {
    auto* ctx = static_cast<UsbCdcContext*>(arg);
    if (ctx->rx_stream == nullptr) return true;
    const size_t sent = xStreamBufferSend(ctx->rx_stream, data, data_len, 0);
    if (sent != data_len) {
        // Stream full: hand the chunk back to the component's RX buffer
        // instead of truncating the protocol-v2 byte stream.
        return false;
    }
    return true;
}

static void cdc_event_cb(const cdc_acm_host_dev_event_data_t* event, void* arg) {
    auto* ctx = static_cast<UsbCdcContext*>(arg);
    if (event->type == CDC_ACM_HOST_DEVICE_DISCONNECTED) {
        // Fired from the component's task; the client task owns close().
        ctx->connected = false;
        ctx->gone = true;
        if (ctx->task_handle != nullptr) xTaskNotifyGive(ctx->task_handle);
    }
}

static void cdc_client_task(void* arg) {
    auto* ctx = static_cast<UsbCdcContext*>(arg);
    LOG_I(TAG, "CDC client task started (vid=0x%04x pid=0x%04x)", ctx->vid, ctx->pid);

    while (ctx->running) {
        if (!ctx->connected.load()) {
            if (ctx->dev != nullptr) {
                cdc_acm_host_close(ctx->dev);
                ctx->dev = nullptr;
            }
            const cdc_acm_host_device_config_t dev_config = {
                .connection_timeout_ms = CDC_OPEN_RETRY_MS,
                .out_buffer_size       = CDC_OUT_BUFFER_SIZE,
                .in_buffer_size        = CDC_IN_BUFFER_SIZE,
                .event_cb              = cdc_event_cb,
                .data_cb               = cdc_data_cb,
                .user_arg              = ctx,
            };
            // Blocks up to connection_timeout_ms waiting for a matching
            // device; that wait doubles as the unplugged poll interval.
            esp_err_t err = cdc_acm_host_open(ctx->vid, ctx->pid, 0, &dev_config, &ctx->dev);
            if (err == ESP_OK) {
                ctx->gone = false;
                if (ctx->rx_stream != nullptr) xStreamBufferReset(ctx->rx_stream);
                ctx->connected = true;
                // Assert DTR for firmware that gates its console on it;
                // AetherLink itself treats bus attachment as authoritative.
                cdc_acm_host_set_control_line_state(ctx->dev, true, false);
                LOG_I(TAG, "CDC-ACM accessory open");
            } else if (err != ESP_ERR_NOT_FOUND && err != ESP_ERR_TIMEOUT) {
                LOG_W(TAG, "cdc_acm_host_open: %s", esp_err_to_name(err));
            }
        } else {
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(200));
            if (ctx->gone.load()) {
                ctx->connected = false;
                LOG_I(TAG, "CDC-ACM accessory disconnected");
            }
        }
    }

    if (ctx->dev != nullptr) {
        ctx->connected = false;
        cdc_acm_host_close(ctx->dev);
        ctx->dev = nullptr;
    }

    LOG_I(TAG, "CDC client task stopped");
    xSemaphoreGive(ctx->task_done);
    vTaskDelete(nullptr);
}

static bool api_is_connected(struct Device* device) {
    auto* ctx = static_cast<UsbCdcContext*>(device_get_driver_data(device));
    return ctx && ctx->connected.load();
}

static int api_read(struct Device* device, uint8_t* data, size_t capacity, uint32_t timeout_ms) {
    auto* ctx = static_cast<UsbCdcContext*>(device_get_driver_data(device));
    if (!ctx || data == nullptr || capacity == 0) return -1;
    if (!ctx->connected.load() || ctx->rx_stream == nullptr) {
        // Keep the service poll cadence responsive while unplugged.
        vTaskDelay(pdMS_TO_TICKS(timeout_ms < 10 ? timeout_ms : 10));
        return -1;
    }
    const size_t received = xStreamBufferReceive(ctx->rx_stream, data, capacity,
                                                 pdMS_TO_TICKS(timeout_ms));
    return static_cast<int>(received);
}

static int api_write(struct Device* device, const uint8_t* data, size_t length, uint32_t timeout_ms) {
    auto* ctx = static_cast<UsbCdcContext*>(device_get_driver_data(device));
    if (!ctx || data == nullptr || length == 0) return -1;
    if (!ctx->connected.load() || ctx->dev == nullptr) return -1;
    const esp_err_t err = cdc_acm_host_data_tx_blocking(ctx->dev, data, length, timeout_ms);
    return err == ESP_OK ? static_cast<int>(length) : 0;
}

static const UsbCdcApi cdc_api = {
    .is_connected = api_is_connected,
    .read         = api_read,
    .write        = api_write,
};

extern "C" {

static error_t start_device(struct Device* device) {
    auto* cfg = GET_CONFIG(device);
    if (!cfg) {
        LOG_E(TAG, "device config is null");
        return ERROR_INVALID_ARGUMENT;
    }

    auto* ctx = new UsbCdcContext();
    ctx->vid = static_cast<uint16_t>(cfg->vid);
    ctx->pid = static_cast<uint16_t>(cfg->pid);

    ctx->rx_stream = xStreamBufferCreate(CDC_RX_STREAM_SIZE, 1);
    if (!ctx->rx_stream) {
        LOG_E(TAG, "failed to create RX stream buffer");
        delete ctx;
        return ERROR_RESOURCE;
    }

    ctx->task_done = xSemaphoreCreateBinary();
    if (!ctx->task_done) {
        LOG_E(TAG, "failed to create task done semaphore");
        vStreamBufferDelete(ctx->rx_stream);
        delete ctx;
        return ERROR_RESOURCE;
    }

    // Requires the parent usbhost0 device (shared usb_host_install) to be
    // started first; devicetree lists parents before children.
    const cdc_acm_host_driver_config_t driver_config = {
        .driver_task_stack_size = 4096,
        .driver_task_priority   = CDC_TASK_PRIORITY,
        .xCoreID                = tskNO_AFFINITY,
        .new_dev_cb             = nullptr,
    };
    esp_err_t err = cdc_acm_host_install(&driver_config);
    if (err != ESP_OK) {
        LOG_E(TAG, "cdc_acm_host_install failed: %s", esp_err_to_name(err));
        vSemaphoreDelete(ctx->task_done);
        vStreamBufferDelete(ctx->rx_stream);
        delete ctx;
        return ERROR_RESOURCE;
    }

    ctx->running = true;
    BaseType_t result = xTaskCreate(cdc_client_task, "usb_cdc_client", CDC_TASK_STACK,
                                    ctx, CDC_TASK_PRIORITY, &ctx->task_handle);
    if (result != pdPASS) {
        LOG_E(TAG, "failed to create usb_cdc_client task");
        ctx->running = false;
        cdc_acm_host_uninstall();
        vSemaphoreDelete(ctx->task_done);
        vStreamBufferDelete(ctx->rx_stream);
        delete ctx;
        return ERROR_RESOURCE;
    }

    device_set_driver_data(device, ctx);
    LOG_I(TAG, "started");
    return ERROR_NONE;
}

static error_t stop_device(struct Device* device) {
    auto* ctx = static_cast<UsbCdcContext*>(device_get_driver_data(device));
    if (!ctx) return ERROR_NONE;

    ctx->running = false;
    // cdc_acm_host_open() returns within CDC_OPEN_RETRY_MS, so the task
    // observes the stop flag without an unblock primitive.
    if (xSemaphoreTake(ctx->task_done, pdMS_TO_TICKS(CDC_STOP_TIMEOUT_MS)) != pdTRUE) {
        LOG_E(TAG, "CDC client task stop timed out after %dms", CDC_STOP_TIMEOUT_MS);
        vTaskDelete(ctx->task_handle);
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    ctx->task_handle = nullptr;
    vSemaphoreDelete(ctx->task_done);

    const esp_err_t uninstall_err = cdc_acm_host_uninstall();
    if (uninstall_err != ESP_OK) {
        LOG_W(TAG, "cdc_acm_host_uninstall: %s", esp_err_to_name(uninstall_err));
    }

    vStreamBufferDelete(ctx->rx_stream);
    device_set_driver_data(device, nullptr);
    delete ctx;
    LOG_I(TAG, "stopped");
    return ERROR_NONE;
}

Driver esp32_usbhost_cdc_driver = {
    .name         = "esp32_usbhost_cdc",
    .compatible   = (const char*[]) { "espressif,esp32-usbhost-cdc", nullptr },
    .start_device = start_device,
    .stop_device  = stop_device,
    .api          = &cdc_api,
    .device_type  = &USB_HOST_CDC_TYPE,
    .owner        = nullptr,
    .internal     = nullptr,
};

} // extern "C"

#endif // CONFIG_SOC_USB_OTG_SUPPORTED
