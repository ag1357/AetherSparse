#pragma once

#include <stdint.h>

static inline int64_t esp_timer_get_time(void) {
  static int64_t now = 0;
  return ++now;
}
