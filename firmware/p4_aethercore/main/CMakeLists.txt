idf_component_register(
  SRCS "main.cpp" "pack_io.cpp" "pack_v2.cpp" "trace_runner.cpp"
       "service_runtime.cpp" "link_tcp.cpp"
       "memory/memory_native.cpp"
       "protocol/protocol_v2.cpp"
       "service/sha256.cpp" "service/service_records.cpp" "service/service_core.cpp"
       "link/slip_link.cpp" "link/slip_uart.cpp"
  REQUIRES esp_hosted esp_wifi_remote esp_wifi nvs_flash esp_netif esp_event
           esp_timer driver sdmmc fatfs
  INCLUDE_DIRS "." "memory" "protocol" "link"
  REQUIRES aethercore_runtime esp_timer esp_psram esp_hw_support spi_flash
           sdmmc fatfs vfs json mbedtls driver
)
