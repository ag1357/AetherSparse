// See the header for the transport overview.
//
// Wi-Fi service interaction notes (investigated, not modified):
// - tt::service::wifi wraps the kernel "esp32-wifi-pinned" driver, which owns
//   esp_wifi (esp_wifi_remote -> hosted C6). esp_wifi_init() is idempotent in
//   IDF 5.5.x (returns ESP_OK when already initialized), so the service can
//   safely take over the radio even if another consumer initialized it first.
// - The WebServer service, when enabled in settings, starts its own softAP on
//   192.168.4.1 (the same address Device B serves on) and forces the radio
//   into AP-only mode. Both make the STA link impossible, so start() stops the
//   WebServer service while AetherChat runs and stop() restarts it. No
//   WebServer code is modified.
// - The WiFi service's periodic auto-connect scan could steer the radio to a
//   saved home AP between our retries, so the public setAutoScanPaused(true)
//   hook is held for the app's lifetime (it is the service's designed
//   mechanism for exactly this kind of window).
// - The kernel driver does not auto-reconnect by itself; all (re)association
//   is driven from here via service::wifi::connect().
#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <Tactility/app/aetherchat/AetherLinkTcp.h>

#include <Tactility/service/ServiceRegistration.h>

#include <tactility/log.h>

#include <lwip/inet.h>
#include <lwip/sockets.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <new>

namespace tt::app::aetherchat {

namespace {

constexpr auto* TAG = "AetherLinkTcp";
constexpr uint32_t RADIO_ON_TIMEOUT_MS = 15000;
constexpr uint32_t ASSOCIATE_TIMEOUT_MS = 6000;
constexpr uint32_t TCP_CONNECT_TIMEOUT_MS = 3000;
constexpr uint32_t RECV_TICK_MS = 500;
constexpr size_t RX_BUFFER_BYTES = 4 + AetherLinkTcp::MAX_FRAME_BYTES;

void putLengthHeader(uint8_t* out, uint32_t length) {
    out[0] = static_cast<uint8_t>((length >> 24U) & 0xFFU);
    out[1] = static_cast<uint8_t>((length >> 16U) & 0xFFU);
    out[2] = static_cast<uint8_t>((length >> 8U) & 0xFFU);
    out[3] = static_cast<uint8_t>(length & 0xFFU);
}

uint32_t getLengthHeader(const uint8_t* in) {
    return (static_cast<uint32_t>(in[0]) << 24U) |
        (static_cast<uint32_t>(in[1]) << 16U) |
        (static_cast<uint32_t>(in[2]) << 8U) |
        static_cast<uint32_t>(in[3]);
}

bool sendAll(int fd, const uint8_t* data, size_t length) {
    size_t sent = 0;
    while (sent < length) {
        const int result = send(fd, data + sent, length - sent, 0);
        if (result <= 0) {
            return false;
        }
        sent += static_cast<size_t>(result);
    }
    return true;
}

} // namespace

void AetherLinkTcp::setState(const char* text) const {
    LOG_I(TAG, "%s", text);
    if (callbacks.onState) {
        callbacks.onState(text);
    }
}

bool AetherLinkTcp::stopRequested(uint32_t delayMs) const {
    // Stop-aware sleep. Mask strictly: EVENT_GOT_IP may also be set.
    const EventBits_t bits = xEventGroupWaitBits(events, EVENT_STOP, pdFALSE, pdFALSE, pdMS_TO_TICKS(delayMs));
    return (bits & EVENT_STOP) != 0;
}

uint32_t AetherLinkTcp::sleepBackoff(uint32_t backoffMs) const {
    char text[40];
    std::snprintf(text, sizeof(text), "Link: retry in %lus", (unsigned long)(backoffMs / 1000));
    setState(text);
    stopRequested(backoffMs);
    return backoffMs >= BACKOFF_MAX_MS ? BACKOFF_MAX_MS : backoffMs + 1000;
}

void AetherLinkTcp::onWifiEvent(service::wifi::WifiEvent event) {
    // Runs on the esp_event task (synchronous pubsub publish): only flip bits.
    switch (event.type) {
        case WIFI_EVENT_TYPE_STATION_CONNECTION_RESULT:
            if (event.connection_error == WIFI_STATION_CONNECTION_ERROR_NONE) {
                xEventGroupSetBits(events, EVENT_GOT_IP);
            }
            break;
        case WIFI_EVENT_TYPE_STATION_STATE_CHANGED:
            if (event.station_state == WIFI_STATION_STATE_DISCONNECTED) {
                xEventGroupClearBits(events, EVENT_GOT_IP);
            }
            break;
        default:
            break;
    }
}

bool AetherLinkTcp::waitForRadio(uint32_t timeoutMs) {
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeoutMs);
    while (!stopping) {
        const auto state = service::wifi::getRadioState();
        if (state != service::wifi::RadioState::Off && state != service::wifi::RadioState::OffPending) {
            return true;
        }
        // (Re)issue the enable request; it is ignored when already pending/on.
        service::wifi::setEnabled(true);
        enabledWifi = true;
        if (xTaskGetTickCount() >= deadline) {
            return false;
        }
        if (stopRequested(250)) {
            return false;
        }
    }
    return false;
}

bool AetherLinkTcp::ensureAssociated() {
    if (!waitForRadio(RADIO_ON_TIMEOUT_MS)) {
        return false;
    }

    if (service::wifi::getRadioState() == service::wifi::RadioState::ConnectionActive &&
        service::wifi::getConnectionTarget() == LINK_AP_SSID) {
        return true;
    }

    setState("Link: associating");
    const service::wifi::settings::WifiApSettings ap(LINK_AP_SSID, LINK_AP_PASSWORD, false, 0);
    service::wifi::connect(ap, /*remember=*/false);

    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(ASSOCIATE_TIMEOUT_MS);
    while (!stopping && xTaskGetTickCount() < deadline) {
        if ((xEventGroupGetBits(events) & EVENT_GOT_IP) != 0) {
            const std::string ip = service::wifi::getIp();
            char text[48];
            std::snprintf(text, sizeof(text), "Link: associated (%s)", ip.c_str());
            setState(text);
            return true;
        }
        if (stopRequested(100)) {
            return false;
        }
    }
    return false;
}

int AetherLinkTcp::tcpConnect() {
    const int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (fd < 0) {
        LOG_W(TAG, "socket() failed: errno %d", errno);
        return -1;
    }

    const int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);

    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(DEVICE_B_PORT);
    address.sin_addr.s_addr = inet_addr(DEVICE_B_HOST);

    bool connected = connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0;
    if (!connected && errno == EINPROGRESS) {
        const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(TCP_CONNECT_TIMEOUT_MS);
        while (!stopping && xTaskGetTickCount() < deadline) {
            fd_set writeSet;
            FD_ZERO(&writeSet);
            FD_SET(fd, &writeSet);
            timeval timeout = { .tv_sec = 0, .tv_usec = 250 * 1000 };
            const int ready = select(fd + 1, nullptr, &writeSet, nullptr, &timeout);
            if (ready > 0) {
                int socketError = 0;
                socklen_t errorLength = sizeof(socketError);
                getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &errorLength);
                connected = socketError == 0;
                break;
            }
            if (ready < 0) {
                break;
            }
            // select timeout: poll again until the deadline.
        }
    }
    fcntl(fd, F_SETFL, flags);

    if (!connected) {
        close(fd);
        return -1;
    }

    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    const timeval receiveTimeout = { .tv_sec = 0, .tv_usec = RECV_TICK_MS * 1000 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &receiveTimeout, sizeof(receiveTimeout));
    const timeval sendTimeout = { .tv_sec = 3, .tv_usec = 0 };
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &sendTimeout, sizeof(sendTimeout));
    return fd;
}

void AetherLinkTcp::readLoop(int fd) {
    rxUsed = 0;
    while (!stopping) {
        // Bail out promptly when the association dropped: a half-open TCP
        // connection would otherwise block recv() indefinitely.
        if ((xEventGroupGetBits(events) & EVENT_GOT_IP) == 0) {
            LOG_W(TAG, "Wi-Fi association lost while connected");
            return;
        }
        const size_t remaining = RX_BUFFER_BYTES - rxUsed;
        if (remaining == 0) {
            // Unreachable while the 16 KiB frame cap is enforced below.
            LOG_E(TAG, "RX buffer full without a complete frame; dropping link");
            return;
        }
        const int received = recv(fd, rxBuffer + rxUsed, remaining, 0);
        if (received == 0) {
            return; // clean close by Device B
        }
        if (received < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                continue; // receive tick
            }
            LOG_W(TAG, "recv() failed: errno %d", errno);
            return;
        }
        rxUsed += static_cast<size_t>(received);

        size_t offset = 0;
        for (;;) {
            if (rxUsed - offset < 4) {
                break;
            }
            const uint32_t length = getLengthHeader(rxBuffer + offset);
            if (length == 0 || length > MAX_FRAME_BYTES) {
                LOG_E(TAG, "Malformed frame header (len %lu); dropping link", (unsigned long)length);
                return;
            }
            if (rxUsed - offset < 4 + length) {
                break; // wait for the rest of the frame
            }
            if (callbacks.onFrame) {
                callbacks.onFrame(reinterpret_cast<const char*>(rxBuffer + offset + 4), length);
            }
            offset += 4 + length;
        }
        if (offset > 0) {
            std::memmove(rxBuffer, rxBuffer + offset, rxUsed - offset);
            rxUsed -= offset;
        }
    }
}

void AetherLinkTcp::closeSocket() {
    xSemaphoreTake(sendMutex, portMAX_DELAY);
    const int fd = socketFd;
    socketFd = -1;
    xSemaphoreGive(sendMutex);
    if (fd >= 0) {
        // The link task owns the fd; only wake its recv()/connect() here and
        // let it close the socket itself to avoid fd-reuse races.
        shutdown(fd, SHUT_RDWR);
    }
}

void AetherLinkTcp::taskMain() {
    rxBuffer = new (std::nothrow) uint8_t[RX_BUFFER_BYTES];
    uint32_t backoffMs = BACKOFF_MIN_MS;

    while (!stopping) {
        if (!ensureAssociated()) {
            if (!stopping) {
                backoffMs = sleepBackoff(backoffMs);
            }
            continue;
        }

        setState("Link: connecting");
        const int fd = tcpConnect();
        if (fd < 0) {
            if (!stopping) {
                backoffMs = sleepBackoff(backoffMs);
            }
            continue;
        }
        backoffMs = BACKOFF_MIN_MS;

        xSemaphoreTake(sendMutex, portMAX_DELAY);
        socketFd = fd;
        xSemaphoreGive(sendMutex);

        setState("Link: up");
        if (callbacks.onLinkUp) {
            callbacks.onLinkUp(); // the app sends SESSION_OPEN/RESUME here
        }

        readLoop(fd);

        xSemaphoreTake(sendMutex, portMAX_DELAY);
        if (socketFd == fd) {
            socketFd = -1;
        }
        xSemaphoreGive(sendMutex);
        close(fd);

        // Partial-frame state dies with the connection (spec); Device B's
        // session state is session_id-keyed and survives.
        rxUsed = 0;
        if (callbacks.onLinkDown) {
            callbacks.onLinkDown();
        }
        if (!stopping) {
            setState("Link: down");
        }
    }

    delete[] rxBuffer;
    rxBuffer = nullptr;
    xSemaphoreGive(doneSemaphore);
    vTaskDelete(nullptr);
}

void AetherLinkTcp::taskEntry(void* context) {
    static_cast<AetherLinkTcp*>(context)->taskMain();
}

bool AetherLinkTcp::start(const Callbacks& callbacksValue) {
    if (task != nullptr) {
        return false;
    }
    callbacks = callbacksValue;
    stopping = false;

    events = xEventGroupCreate();
    sendMutex = xSemaphoreCreateMutex();
    doneSemaphore = xSemaphoreCreateBinary();
    if (events == nullptr || sendMutex == nullptr || doneSemaphore == nullptr) {
        LOG_E(TAG, "Failed to allocate sync primitives");
        return false;
    }

    // The WebServer service (when enabled in settings) squats on 192.168.4.1
    // with its own softAP and forces AP-only radio mode; both conflict with
    // the Device B link. Stop it while AetherChat runs; restarted in stop().
    if (service::findManifestById("WebServer") != nullptr &&
        service::getState("WebServer") == SERVICE_STATE_STARTED) {
        if (service::stopService("WebServer")) {
            stoppedWebServer = true;
            LOG_I(TAG, "Stopped WebServer service (would conflict with the Device B link)");
        } else {
            LOG_W(TAG, "Failed to stop WebServer service; link may not work");
        }
    }

    // Keep the WiFi service's auto-connect scan from steering the radio away
    // from the bench AP between our retries.
    service::wifi::setAutoScanPaused(true);
    pausedAutoScan = true;

    wifiSubscription = service::wifi::getPubsub()->subscribe(
        [this](service::wifi::WifiEvent event) { onWifiEvent(event); }
    );

    if (stoppedWebServer) {
        // Stopping WebServer stops the slave's Wi-Fi with it while the
        // service's radio state still reads On, so a plain setEnabled(true)
        // is ignored and station connects fail on the slave with
        // ESP_ERR_WIFI_NOT_STARTED (hosted resp 0x3002). Force a clean radio
        // re-init (the two calls dispatch in order on the service thread).
        service::wifi::setEnabled(false);
        service::wifi::setEnabled(true);
        enabledWifi = true;
    } else if (service::wifi::getRadioState() == service::wifi::RadioState::Off) {
        service::wifi::setEnabled(true);
        enabledWifi = true;
    }

    if (xTaskCreate(taskEntry, "aetherlink_tcp", 8192, this, 5, &task) != pdPASS) {
        LOG_E(TAG, "Failed to start link task");
        task = nullptr;
        vEventGroupDelete(events);
        events = nullptr;
        vSemaphoreDelete(sendMutex);
        sendMutex = nullptr;
        vSemaphoreDelete(doneSemaphore);
        doneSemaphore = nullptr;
        return false;
    }
    return true;
}

void AetherLinkTcp::stop() {
    if (task == nullptr) {
        return;
    }
    stopping = true;
    xEventGroupSetBits(events, EVENT_STOP);
    closeSocket(); // wake a blocked recv()/connect() promptly
    if (xSemaphoreTake(doneSemaphore, pdMS_TO_TICKS(5000)) != pdPASS) {
        LOG_E(TAG, "Link task did not stop in time");
    }

    if (wifiSubscription != nullptr) {
        service::wifi::getPubsub()->unsubscribe(wifiSubscription);
        wifiSubscription = nullptr;
    }
    if (pausedAutoScan) {
        service::wifi::setAutoScanPaused(false);
        pausedAutoScan = false;
    }

    // Drop the association we drove (this also pauses the service's
    // auto-connect until the user connects to something else).
    service::wifi::disconnect();

    if (enabledWifi) {
        service::wifi::setEnabled(false);
        enabledWifi = false;
    }
    if (stoppedWebServer) {
        service::startService("WebServer");
        stoppedWebServer = false;
    }

    vEventGroupDelete(events);
    events = nullptr;
    vSemaphoreDelete(sendMutex);
    sendMutex = nullptr;
    vSemaphoreDelete(doneSemaphore);
    doneSemaphore = nullptr;
    task = nullptr;
}

bool AetherLinkTcp::isConnected() const {
    return socketFd >= 0;
}

bool AetherLinkTcp::sendFrame(const uint8_t* data, size_t length) {
    if (data == nullptr || length == 0 || length > MAX_FRAME_BYTES) {
        return false;
    }
    uint8_t header[4];
    putLengthHeader(header, static_cast<uint32_t>(length));

    xSemaphoreTake(sendMutex, portMAX_DELAY);
    const int fd = socketFd;
    const bool ok = fd >= 0 && sendAll(fd, header, sizeof(header)) && sendAll(fd, data, length);
    xSemaphoreGive(sendMutex);
    if (!ok) {
        LOG_W(TAG, "sendFrame failed (%u bytes)", (unsigned)length);
    }
    return ok;
}

} // namespace tt::app::aetherchat

#endif // CONFIG_SOC_WIFI_SUPPORTED || CONFIG_SLAVE_SOC_WIFI_SUPPORTED
