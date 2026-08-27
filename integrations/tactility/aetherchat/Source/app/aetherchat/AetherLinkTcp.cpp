#ifdef ESP_PLATFORM
#include <sdkconfig.h>
#endif

#if defined(CONFIG_SOC_WIFI_SUPPORTED) || defined(CONFIG_SLAVE_SOC_WIFI_SUPPORTED)

#include <Tactility/app/aetherchat/AetherLinkTcp.h>

#include <Tactility/service/webserver/WebServerService.h>
#include <Tactility/settings/WebServerSettings.h>

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
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(delayMs);
    while (!stopping && xTaskGetTickCount() < deadline) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return stopping;
}

bool AetherLinkTcp::isTactilityApReady() const {
    const auto webSettings = settings::webserver::loadOrGetDefault();
    return webSettings.wifiEnabled &&
        webSettings.webServerEnabled &&
        webSettings.wifiMode == settings::webserver::WiFiMode::AccessPoint &&
        service::webserver::isWebServerEnabled();
}

int AetherLinkTcp::openListener() const {
    const int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (fd < 0) {
        LOG_W(TAG, "listener socket() failed: errno %d", errno);
        return -1;
    }

    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    sockaddr_in address = {};
    address.sin_family = AF_INET;
    address.sin_port = htons(LISTEN_PORT);
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        LOG_W(TAG, "bind(:%u) failed: errno %d", (unsigned)LISTEN_PORT, errno);
        close(fd);
        return -1;
    }

    // One Device-B connection is authoritative. A backlog of one bounds a
    // concurrent reconnect while the current client is being torn down.
    if (listen(fd, 1) != 0) {
        LOG_W(TAG, "listen(:%u) failed: errno %d", (unsigned)LISTEN_PORT, errno);
        close(fd);
        return -1;
    }

    const int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }
    return fd;
}

int AetherLinkTcp::acceptClient(int fd) const {
    while (!stopping && isTactilityApReady()) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(fd, &readSet);
        timeval timeout = { .tv_sec = 0, .tv_usec = IO_TICK_MS * 1000 };
        const int ready = select(fd + 1, &readSet, nullptr, nullptr, &timeout);
        if (ready == 0) {
            continue;
        }
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }

        sockaddr_in peer = {};
        socklen_t peerLength = sizeof(peer);
        const int client = accept(fd, reinterpret_cast<sockaddr*>(&peer), &peerLength);
        if (client < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                continue;
            }
            return -1;
        }

        int one = 1;
        setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        const timeval receiveTimeout = { .tv_sec = 0, .tv_usec = IO_TICK_MS * 1000 };
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &receiveTimeout, sizeof(receiveTimeout));
        const timeval sendTimeout = { .tv_sec = 3, .tv_usec = 0 };
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &sendTimeout, sizeof(sendTimeout));
        return client;
    }
    return -1;
}

void AetherLinkTcp::readLoop(int fd) {
    rxUsed = 0;
    while (!stopping && isTactilityApReady()) {
        const size_t remaining = RX_BUFFER_BYTES - rxUsed;
        if (remaining == 0) {
            LOG_E(TAG, "RX buffer full without a complete frame; dropping link");
            return;
        }

        const int received = recv(fd, rxBuffer + rxUsed, remaining, 0);
        if (received == 0) {
            return;
        }
        if (received < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                continue;
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
                break;
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

void AetherLinkTcp::wakeSockets() {
    xSemaphoreTake(sendMutex, portMAX_DELAY);
    if (socketFd >= 0) {
        shutdown(socketFd, SHUT_RDWR);
    }
    if (listenerFd >= 0) {
        shutdown(listenerFd, SHUT_RDWR);
    }
    xSemaphoreGive(sendMutex);
}

void AetherLinkTcp::taskMain() {
    rxBuffer = new (std::nothrow) uint8_t[RX_BUFFER_BYTES];
    if (rxBuffer == nullptr) {
        setState("Link: memory unavailable");
        xSemaphoreGive(doneSemaphore);
        vTaskDelete(nullptr);
        return;
    }

    while (!stopping) {
        if (!isTactilityApReady()) {
            setState("Enable Tactility Web Server / Access Point");
            stopRequested(RETRY_MS);
            continue;
        }

        const auto webSettings = settings::webserver::loadOrGetDefault();
        LOG_I(TAG, "Using Tactility-owned AP '%s' channel %u; password not logged",
            webSettings.apSsid.c_str(), (unsigned)webSettings.apChannel);

        const int listenFd = openListener();
        if (listenFd < 0) {
            setState("Link: listener unavailable");
            stopRequested(RETRY_MS);
            continue;
        }
        xSemaphoreTake(sendMutex, portMAX_DELAY);
        listenerFd = listenFd;
        xSemaphoreGive(sendMutex);
        setState("Link: listening on Tactility AP");

        while (!stopping && isTactilityApReady()) {
            const int clientFd = acceptClient(listenFd);
            if (clientFd < 0) {
                break;
            }

            xSemaphoreTake(sendMutex, portMAX_DELAY);
            socketFd = clientFd;
            xSemaphoreGive(sendMutex);
            setState("Link: Device B connected");
            if (callbacks.onLinkUp) {
                callbacks.onLinkUp();
            }

            readLoop(clientFd);

            xSemaphoreTake(sendMutex, portMAX_DELAY);
            if (socketFd == clientFd) {
                socketFd = -1;
            }
            xSemaphoreGive(sendMutex);
            close(clientFd);

            // Protocol-v2 requires partial input to die with the connection.
            rxUsed = 0;
            if (callbacks.onLinkDown) {
                callbacks.onLinkDown();
            }
            if (!stopping && isTactilityApReady()) {
                setState("Link: waiting for Device B reconnect");
            }
        }

        xSemaphoreTake(sendMutex, portMAX_DELAY);
        if (listenerFd == listenFd) {
            listenerFd = -1;
        }
        xSemaphoreGive(sendMutex);
        close(listenFd);
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

    sendMutex = xSemaphoreCreateMutex();
    doneSemaphore = xSemaphoreCreateBinary();
    if (sendMutex == nullptr || doneSemaphore == nullptr) {
        LOG_E(TAG, "Failed to allocate sync primitives");
        if (sendMutex != nullptr) {
            vSemaphoreDelete(sendMutex);
            sendMutex = nullptr;
        }
        if (doneSemaphore != nullptr) {
            vSemaphoreDelete(doneSemaphore);
            doneSemaphore = nullptr;
        }
        return false;
    }

    // Deliberately no WebServer or Wi-Fi mutation here. The task waits until
    // Tactility's configured AP/WebServer is ready, then binds port 9000.
    if (xTaskCreate(taskEntry, "aetherlink_tcp", 8192, this, 5, &task) != pdPASS) {
        LOG_E(TAG, "Failed to start link task");
        task = nullptr;
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
    wakeSockets();
    if (xSemaphoreTake(doneSemaphore, pdMS_TO_TICKS(5000)) != pdPASS) {
        LOG_E(TAG, "Link task did not stop in time");
        return;
    }

    // Deliberately close only AetherLink resources. Tactility retains its AP,
    // DHCP, HTTP server, radio state, saved networks and auto-scan policy.
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
    if (data == nullptr || length == 0 || length > MAX_FRAME_BYTES || stopping) {
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
