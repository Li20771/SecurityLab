#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int main(void) {
    int result = add(10, 32);
    printf("ARM64 result = %d\n", result);
    return 0;
}
