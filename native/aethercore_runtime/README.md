# AetherCore portable runtime ABI v1

This directory contains the runtime-critical, allocation-free C++17 core behind
a C-compatible ABI. It deliberately excludes corpus compilation and policy
training. The ABI covers canonical candidate union/cap, an external int8 linear
policy, exact bounded actions, verification, and session serialization.

Host build without generated products in the repository:

```sh
make -C native/aethercore_runtime
```

Or with CMake:

```sh
cmake -S native/aethercore_runtime -B /tmp/aethercore-runtime \
  -DAETHERCORE_BUILD_SHARED=ON
cmake --build /tmp/aethercore-runtime
```

For ESP-IDF, add `native/aethercore_runtime/esp-idf` as a component directory,
or copy/symlink `native/aethercore_runtime` into the application's components
directory and use the supplied component `CMakeLists.txt`. The core requires no
filesystem, threads, exceptions, RTTI, heap allocation, or OS services. The
platform layer supplies page reads, policy weights, and transport.

V14 adds fixed-width compact COG, 5C root constraints, sparse-specialist
descriptors, progress/stagnation counters, and a 64-action int8 controller
binding. These are additive: the V13 session and paged-address ABI remains
unchanged. The native COG field order exactly matches the 19-u16
`CompactCOGView.packed_u16()` contract, without importing Python at build time.

The ABI is fixed-width and versioned. Session persistence is a canonical
little-endian byte stream with CRC-32; it is not a dump of compiler-dependent
struct padding. The V14 cognitive snapshot follows the same rule.

V15 hardens the same ABI without changing its legal V14 wire image: selected
evidence is pinned against K=32 eviction, VERIFIED and TERMINAL workspaces reject
candidate mutation, session decoding revalidates runtime invariants, and
`ac_cog_runtime_deserialize_v1` decodes the exact 180-byte COG projection without
raw struct casts. The production-facing host entrypoints are:

```sh
aethersparse aethercore compile
aethersparse aethercore pack --help
aethersparse aethercore qualify
aethersparse aethercore service
```

Historical mission scripts remain available for published-result
reproducibility; these four commands are the consolidated V15 operational path.
