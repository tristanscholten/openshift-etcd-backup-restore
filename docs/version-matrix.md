# Version matrix

| OCP input | Backup selection | Result |
|---|---|---|
| `4.20.z` on all inventory hosts | metadata is same exact `4.20.z` | eligible for preflight |
| `4.20.z1` and `4.20.z2` | any | reject |
| `4.21.*` or later | any | reject |
| unknown/missing version | any | reject |
| pair hash or metadata mismatch | any | reject |

No compatibility claim is made for any OCP version outside exact OCP 4.20 z-stream equality. Add documentation, source review, and disposable-lab evidence before extending this matrix.
