# Phase 9 host parity harness for the native service core.
#
# Standalone C++17 build: no ESP-IDF, no exceptions, no RTTI — the same
# constraints the firmware component imposes on main/service/.  The IDF
# wire-in (main/CMakeLists.txt) is owned by the primary session and happens
# later; nothing under main/ is edited here.
cmake_minimum_required(VERSION 3.16)
project(p4_aethercore_service_host_test CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(service_parity
  service_parity.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/service/service_core.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/service/service_records.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/service/sha256.cpp
)
target_include_directories(service_parity PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../main)
target_compile_options(service_parity PRIVATE
  -Wall -Wextra -Werror -fno-exceptions -fno-rtti)
