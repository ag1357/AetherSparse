CXX ?= c++
CPPFLAGS += -Iinclude -DAETHERCORE_RUNTIME_BUILD
CXXFLAGS ?= -O2 -std=c++17 -fno-exceptions -fno-rtti -Wall -Wextra -Werror

.PHONY: all clean
all: libaethercore_runtime.so

libaethercore_runtime.so: src/aethercore_runtime.cpp include/aethercore_runtime.h
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -fPIC -shared src/aethercore_runtime.cpp -o $@

clean:
	$(RM) libaethercore_runtime.so
