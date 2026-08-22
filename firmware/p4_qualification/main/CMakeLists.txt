idf_component_register(
  SRCS "main.cpp"
  INCLUDE_DIRS "."
  REQUIRES aethercore_runtime esp_timer esp_psram esp_hw_support spi_flash
)
