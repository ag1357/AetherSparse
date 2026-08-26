idf_component_register(
  SRCS "main.cpp"
  INCLUDE_DIRS "."
  REQUIRES esp_timer esp_psram esp_hw_support sdmmc fatfs vfs
)
