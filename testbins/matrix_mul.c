#include <stdio.h>
#define N 100

void multiply(int mat1[][N], int mat2[][N], int res[][N]) {
    int i, j, k;
    for (i = 0; i < N; i++) {
        for (j = 0; j < N; j++) {
            res[i][j] = 0;
            for (k = 0; k < N; k++)
                res[i][j] += mat1[i][k] * mat2[k][j];
        }
    }
}

int main() {
    static int mat1[N][N], mat2[N][N], res[N][N];
    // Deterministic dummy initialization (paper omits this "for brevity").
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) { mat1[i][j] = i + j; mat2[i][j] = i - j; }
    multiply(mat1, mat2, res);
    printf("checksum=%d\n", res[N-1][N-1]);
    return 0;
}
