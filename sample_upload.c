/**
 * sample.c — 上传测试样本
 * 包含若干 GJB 8114 违规，用于测试 codetidy 真实分析
 */
#include <stdio.h>

int g_counter;  // 全局变量未初始化

// 魔数
#define BUF_SIZE 256

void process_data(int flag) {
    char buf[BUF_SIZE];

    // GJB 违规：分支无大括号
    if (flag == 1)
        printf("flag is one\n");

    // GJB 违规：goto 语句
    if (flag < 0)
        goto cleanup;

    printf("processing...\n");

cleanup:
    printf("done\n");
}

int main(void) {
    process_data(1);
    return 0;
}
