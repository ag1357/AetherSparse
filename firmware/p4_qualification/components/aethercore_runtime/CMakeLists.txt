# In-place binding of the repository's frozen portable runtime. Sources are
# referenced, not copied, so the physical build always compiles the exact
# qualified artifact.
idf_component_register(
  SRCS "${CMAKE_CURRENT_SOURCE_DIR}/../../../../native/aethercore_runtime/src/aethercore_runtime.cpp"
  INCLUDE_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/../../../../native/aethercore_runtime/include"
)
target_compile_options(${COMPONENT_LIB} PRIVATE -fno-exceptions -fno-rtti)
