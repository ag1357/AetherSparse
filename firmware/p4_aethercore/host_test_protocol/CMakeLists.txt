# Standalone host test harness for the native protocol v2 codec.
# No ESP-IDF dependency. Alternative plain g++ build:
#   g++ -std=c++17 -O2 -Wall -Wextra \
#       test_protocol_v2.cpp ../main/protocol/protocol_v2.cpp -o test_protocol_v2
cmake_minimum_required(VERSION 3.16)
project(p4_host_test_protocol CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(test_protocol_v2
  test_protocol_v2.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/protocol/protocol_v2.cpp
)
target_compile_options(test_protocol_v2 PRIVATE -Wall -Wextra -Werror)
