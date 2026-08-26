idf_component_register(
  SRCS "main.cpp" "pack_io.cpp" "pack_v2.cpp" "trace_runner.cpp"
       "memory/memory_native.cpp"
       "protocol/protocol_v2.cpp"
       "link/slip_link.cpp" "link/slip_uart.cpp"
  INCLUDE_DIRS "." "memory" "protocol" "link"
  REQUIRES aethercore_runtime esp_timer esp_psram esp_hw_support spi_flash
           sdmmc fatfs vfs json mbedtls driver
)
