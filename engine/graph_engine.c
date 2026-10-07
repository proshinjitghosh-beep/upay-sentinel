/* Upay Sentinel core engine: time-respecting money-trail traversal.
 *
 * For every account v with an inflow, start at v's largest incoming transfer (time t0) and
 * follow outgoing transfers that happen at or after the funds arrived and before t0 + window.
 * Cash-out agents are sinks (not expanded). Outputs per account:
 *   reach     distinct accounts the money reached
 *   depth     longest hop count of the trail
 *   cash_amt  total value that landed on cash-out agents
 *   span      seconds from t0 to the last cash-out (-1 if none)
 *   dwell     seconds from t0 to the account's first onward transfer (-1 if none)
 */
#include <stdlib.h>
#include <string.h>

void trace_flows(int n, int m, const int *src, const int *dst, const double *amt,
                 const long long *ts, const unsigned char *cash, long long window,
                 int *reach, int *depth, double *cash_amt, long long *span, long long *dwell)
{
    int *head = calloc(n + 1, sizeof(int));
    int *fill = malloc(sizeof(int) * (n + 1));
    int *adj = malloc(sizeof(int) * (m > 0 ? m : 1));
    double *best_amt = calloc(n, sizeof(double));
    long long *best_ts = calloc(n, sizeof(long long));
    unsigned char *has_in = calloc(n, 1);
    int *stamp = calloc(n, sizeof(int));
    int *dep = calloc(n, sizeof(int));
    long long *arrive = calloc(n, sizeof(long long));
    int *queue = malloc(sizeof(int) * (n > 0 ? n : 1));

    for (int e = 0; e < m; e++) {
        head[src[e] + 1]++;
        int d = dst[e];
        if (!has_in[d] || amt[e] > best_amt[d]) { has_in[d] = 1; best_amt[d] = amt[e]; best_ts[d] = ts[e]; }
    }
    for (int i = 0; i < n; i++) head[i + 1] += head[i];
    memcpy(fill, head, sizeof(int) * (n + 1));
    for (int e = 0; e < m; e++) adj[fill[src[e]]++] = e;

    for (int v = 0; v < n; v++) {
        reach[v] = 0; depth[v] = 0; cash_amt[v] = 0; span[v] = -1; dwell[v] = -1;
        if (!has_in[v]) continue;
        long long t0 = best_ts[v], tend = t0 + window;
        int tag = v + 1, qh = 0, qt = 0;
        stamp[v] = tag; arrive[v] = t0; dep[v] = 0; queue[qt++] = v;
        while (qh < qt) {
            int u = queue[qh++];
            for (int k = head[u]; k < head[u + 1]; k++) {
                int e = adj[k]; long long t = ts[e];
                if (t < arrive[u] || t > tend) continue;
                int w = dst[e];
                if (u == v) { long long dl = t - t0; if (dwell[v] < 0 || dl < dwell[v]) dwell[v] = dl; }
                if (cash[w]) { cash_amt[v] += amt[e]; if (t - t0 > span[v]) span[v] = t - t0; }
                if (stamp[w] != tag) {
                    stamp[w] = tag; arrive[w] = t; dep[w] = dep[u] + 1;
                    reach[v]++;
                    if (dep[w] > depth[v]) depth[v] = dep[w];
                    if (!cash[w]) queue[qt++] = w;
                }
            }
        }
    }
    free(head); free(fill); free(adj); free(best_amt); free(best_ts);
    free(has_in); free(stamp); free(dep); free(arrive); free(queue);
}
