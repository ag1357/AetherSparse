#pragma once

#include <cstddef>
#include <cstdint>

#include "link/ac20_wire.h"

namespace ac::linkuart {
bool start(int baud, int tx_pin, int rx_pin);
void serve();
void response_sink(void *, ac::link::Ac20Type, uint32_t, uint32_t,
                   const uint8_t *, size_t);
}  // namespace ac::linkuart
