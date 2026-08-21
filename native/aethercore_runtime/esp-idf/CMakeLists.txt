idf_component_register(
    SRCS "../src/aethercore_runtime.cpp"
    INCLUDE_DIRS "../include"
)
target_compile_options(${COMPONENT_LIB} PRIVATE -fno-exceptions -fno-rtti)
