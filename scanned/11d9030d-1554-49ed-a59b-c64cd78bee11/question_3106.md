# Q3106: get_call_frames grows memory without an enforced bound (mem_pool.rs)

## Question
Can an unprivileged attacker entering through deploying an attacker-authored sBPF program and invoking it with crafted instruction data and account lists reach `get_call_frames` in `program-runtime/src/mem_pool.rs` with a missing entry that makes the loader fall back to a default instead of failing, and grow the buffer `get_call_frames` feeds without any eviction bound taking effect, so that the invariant "Every container this path writes into has an enforced capacity or eviction policy." breaks and the result is DoS?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `get_call_frames()` (around line 152)
- Entrypoint: deploying an attacker-authored sBPF program and invoking it with crafted instruction data and account lists
- Attacker controls: a missing entry that makes the loader fall back to a default instead of failing
- Exploit idea: Repeatedly drive `get_call_frames` so a buffer, map, or cache it feeds grows without eviction, exhausting node memory below the cost the attacker pays.
- Invariant to test: Every container this path writes into has an enforced capacity or eviction policy.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Stress the path and assert the container's size plateaus rather than growing linearly with attacker input.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can perform syscall, CPI, memory-mapping, or deserialization work whose real CPU/memory cost far exceeds the compute units charged, degrading nodes below true cost.
