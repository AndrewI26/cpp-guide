#pragma once
#include <cstdio>
inline int g_pass = 0, g_fail = 0;
#define EXPECT(cond) do { if (cond) ++g_pass; else { ++g_fail; std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)
inline int report() { std::printf("%d passed, %d failed\n", g_pass, g_fail); return g_fail != 0; }
