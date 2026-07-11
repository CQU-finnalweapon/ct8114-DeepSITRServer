"""快速测试 C 函数解析器"""
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, r'E:\北航项目\ct8114-docker-v5\_app_code\app')
from dsit_parser import _parse_functions_from_source

test_c = r"""#include <stdio.h>
#include <stdint.h>

// 全局变量
int g_count = 0;
static const char* g_name = "test";

/*
 * 块注释
 * 多行
 */
static int32_t add(int a, int b) {
    return a + b;
}

void print_hello(const char* name)
{
    printf("Hello, %s!\n", name);
}

uint8_t* get_buffer(size_t len)
{
    static uint8_t buf[256];
    return buf;
}

int main(void) {
    int x = add(1, 2);
    print_hello("world");
    if (x > 0) {
        printf("positive\n");
    }
    for (int i = 0; i < 10; i++) {
        x += i;
    }
    return 0;
}

// 单行函数
int get_count(void) { return g_count; }

static inline void reset(void) {}
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.c', delete=False, encoding='utf-8') as f:
    f.write(test_c)
    tmpfile = f.name

fns = _parse_functions_from_source(Path(tmpfile))
print(f'Found {len(fns)} functions:')
for fn in fns:
    print(f'  {fn.name}: L{fn.start_line}-{fn.end_line}, C{fn.start_column}-{fn.end_column}')

os.unlink(tmpfile)

expected = {'add', 'print_hello', 'get_buffer', 'main', 'get_count', 'reset'}
found = {fn.name for fn in fns}
if found == expected:
    print('PASS: All 6 functions found correctly!')
else:
    print(f'FAIL: Expected {expected}, got {found}')
    print(f'Missing: {expected - found}')
    print(f'Extra: {found - expected}')
