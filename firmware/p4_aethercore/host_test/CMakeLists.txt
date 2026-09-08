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

add_library(service_core_host STATIC
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/service/service_core.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/service/service_records.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/service/sha256.cpp
)
target_include_directories(service_core_host PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/../main)
target_compile_options(service_core_host PRIVATE
  -Wall -Wextra -Werror -fno-exceptions -fno-rtti)

add_executable(service_parity service_parity.cpp)
target_link_libraries(service_parity PRIVATE service_core_host)
target_compile_options(service_parity PRIVATE
  -Wall -Wextra -Werror -fno-exceptions -fno-rtti)

add_executable(provider_contract_test provider_contract_test.cpp)
target_link_libraries(provider_contract_test PRIVATE service_core_host)
target_compile_options(provider_contract_test PRIVATE
  -Wall -Wextra -Werror -fno-exceptions -fno-rtti)

enable_testing()
add_test(NAME provider_contract COMMAND provider_contract_test)

add_executable(knowledge_pack_test
  knowledge_pack_test.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/knowledge_pack.cpp
)
target_include_directories(knowledge_pack_test PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/stubs
  ${CMAKE_CURRENT_SOURCE_DIR}/../main)
target_compile_options(knowledge_pack_test PRIVATE
  -Wall -Wextra -Werror -fno-exceptions -fno-rtti)
add_test(NAME knowledge_pack COMMAND knowledge_pack_test)
