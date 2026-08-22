idf_component_register(
  SRCS "main.cpp" "pack_io.cpp" "trace_runner.cpp"
  INCLUDE_DIRS "."
  REQUIRES aethercore_runtime esp_timer esp_psram esp_hw_support spi_flash
           sdmmc fatfs vfs json mbedtls
)
