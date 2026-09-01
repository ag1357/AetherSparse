cmake_minimum_required(VERSION 3.16)
project(p4_host_test_transport CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(test_aetherlink_stream
  test_aetherlink_stream.cpp
  ${CMAKE_CURRENT_SOURCE_DIR}/../main/link/aetherlink_stream.cpp
)
target_include_directories(test_aetherlink_stream PRIVATE ../main/link)
target_compile_options(test_aetherlink_stream PRIVATE -Wall -Wextra -Werror)
