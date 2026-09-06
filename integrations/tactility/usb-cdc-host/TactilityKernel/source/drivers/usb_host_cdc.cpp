#include <tactility/drivers/usb_host_cdc.h>
#include <tactility/device.h>
#include <tactility/driver.h>

#define USB_CDC_API(driver) ((struct UsbCdcApi*)(driver)->api)

extern "C" {

const struct DeviceType USB_HOST_CDC_TYPE = {
    .name = "usb-host-cdc",
};

bool usb_host_cdc_is_connected(struct Device* device) {
    auto* api = USB_CDC_API(device_get_driver(device));
    return api->is_connected(device);
}

int usb_host_cdc_read(struct Device* device, uint8_t* data, size_t capacity, uint32_t timeout_ms) {
    auto* api = USB_CDC_API(device_get_driver(device));
    return api->read(device, data, capacity, timeout_ms);
}

int usb_host_cdc_write(struct Device* device, const uint8_t* data, size_t length, uint32_t timeout_ms) {
    auto* api = USB_CDC_API(device_get_driver(device));
    return api->write(device, data, length, timeout_ms);
}

} // extern "C"
