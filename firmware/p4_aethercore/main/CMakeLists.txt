set(AC_LINK_SRCS "link/aetherlink_stream.cpp")
set(AC_LINK_REQUIRES "")
if(CONFIG_AC_LINK_USB_CDC_DEVICE)
  list(APPEND AC_LINK_SRCS "link_usb_cdc.cpp")
  list(APPEND AC_LINK_REQUIRES esp_tinyusb)
elseif(CONFIG_AC_LINK_UART_FALLBACK)
  list(APPEND AC_LINK_SRCS "link_uart_stream.cpp")
elseif(CONFIG_AC_LINK_DEPRECATED_TCP_DIAGNOSTIC)
  list(APPEND AC_LINK_SRCS "link_tcp.cpp")
  list(APPEND AC_LINK_REQUIRES esp_hosted esp_wifi_remote esp_wifi nvs_flash esp_netif esp_event)
endif()

idf_component_register(
  SRCS "main.cpp" "pack_io.cpp" "pack_v2.cpp" "trace_runner.cpp"
       "service_runtime.cpp" ${AC_LINK_SRCS}
       "memory/memory_native.cpp"
       "protocol/protocol_v2.cpp"
       "service/sha256.cpp" "service/service_records.cpp" "service/service_core.cpp"
       "link/slip_link.cpp" "link/slip_uart.cpp"
  REQUIRES ${AC_LINK_REQUIRES} esp_timer driver sdmmc fatfs
  INCLUDE_DIRS "." "memory" "protocol" "link"
  PRIV_REQUIRES aethercore_runtime esp_psram esp_hw_support spi_flash
                vfs json mbedtls
)
