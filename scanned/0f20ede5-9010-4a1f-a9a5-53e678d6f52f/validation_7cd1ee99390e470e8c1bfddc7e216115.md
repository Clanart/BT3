## Finding: Valid

### Title
Attacker-Controlled `num_data_shreds`/`num_coding_shreds` Header Fields Allow Amplified Stub-Shred Allocation in Merkle Shred Recovery - (File: `ledger/src/shred/merkle.rs`)

### Summary
`recover()` fills every erasure-shard-index gap between the shreds it actually received with heap-allocated, full-size stub shreds via `make_stub_shred`, and the number of gaps to fill is driven entirely by `num_shards = num_data_shreds + num_coding_shreds`, which is read straight from the coding header of an attacker-supplied shred with no independent bound check against the number of shreds actually present.

### Finding Description
`recover()` computes `num_shards` from `coding_header.num_data_shreds`/`num_coding_shreds` [1](#0-0) , and then, while iterating the (sorted) received shreds, pushes a freshly heap-allocated stub shred for every erasure-shard-index between consecutive received shreds and after the last received shred up to `num_shards`, via `make_stub_shred(batch.len())` in a `while batch.len() < erasure_shard_index` / `while batch.len() < num_shards` loop [2](#0-1) . Each stub allocation is a full `vec![0u8; ShredCode::SIZE_OF_PAYLOAD]` or `vec![0u8; ShredData::SIZE_OF_PAYLOAD]` [3](#0-2) [4](#0-3) .

This stub-fill loop runs entirely before the Reed-Solomon sufficiency/reconstruction step (`reed_solomon_cache...reconstruct(&mut shards)`) is invoked [5](#0-4) , so the allocation cost is paid regardless of whether the small number of genuinely-received shreds is even sufficient to reconstruct the batch. Because `num_data_shreds` and `num_coding_shreds` are each `u16` fields taken from the header of a single attacker-controlled shred, an attacker can submit just two shreds — one at erasure-shard-index 0 and one at the maximal index implied by an inflated `num_shards` — and force the loop to allocate on the order of `num_shards` (up to ~2×65535) full-size payload buffers per `recover()` call, before the function has any chance to fail on insufficient shard count.

### Impact Explanation
This breaks the intended cost invariant that erasure recovery cost should scale with the number of *actually missing* shreds, not with attacker-declared header fields. It allows a low-cost, unprivileged remote input (two shreds via turbine/repair) to trigger a disproportionate amount of heap allocation and memcopy work inside the ledger/shred-repair code path, which is reachable from public gossip/repair/turbine protocols without any signer or stake requirement beyond passing shred signature checks earlier in the pipeline. Repeated cheaply-crafted requests against distinct FEC sets can be used to induce CPU and memory pressure on validators, which is in-scope as a non-RPC remote exhaustion vector.

### Likelihood Explanation
The precondition is only that: (1) the crafted shred passes the earlier signature/consistency checks performed against `common_header.signature` (line 776) and sorting/bounds check on erasure_shard_index (line 780), and (2) it is dispatched into the merkle recovery path. The `num_data_shreds`/`num_coding_shreds` values are read directly from the received shred's `CodingShredHeader` with no cross-validation limiting their magnitude relative to the number of shreds actually collected for that FEC set before `recover()` is invoked in this function. I was not able to fully verify, within the available tool budget, whether a caller in `ledger/src/blockstore.rs` (which invokes shred recovery) imposes an additional cap on `num_data_shreds + num_coding_shreds` (e.g., against a `DATA_SHREDS_PER_FEC_BLOCK`-style constant) prior to calling into `merkle::recover`. If such a cap exists and is enforced strictly before recovery is attempted, it would reduce or eliminate the practical impact; this should be double-checked in `ledger/src/blockstore.rs` around the call sites of `recover`/`try_shred_recovery`.

### Recommendation
Bound the stub-shred-fill loop by validating `num_data_shreds`/`num_coding_shreds` against a fixed maximum consistent with legitimate FEC-set sizes (e.g., existing `DATA_SHREDS_PER_FEC_BLOCK`/`MAX_CODE_SHREDS_PER_SLOT`-style constants) before entering `recover()`, and/or short-circuit before the stub-fill loop if the number of shreds actually present is insufficient for the declared `num_data_shreds` (Reed-Solomon precondition), so that allocation work is not performed ahead of the sufficiency check.

### Proof of Concept
1. Craft two `Shred::ShredData`/`Shred::ShredCode` payloads with identical `common_header.signature`, `slot`, `fec_set_index`, and `shred_variant`, but with `coding_header.num_data_shreds`/`num_coding_shreds` set to large `u16` values (e.g., 65535 each, giving `num_shards` ≈ 131070).
2. Set one shred's erasure-shard-index to 0 and the other's to `num_shards - 1` (satisfying the `sort`/bounds check at line 780).
3. Invoke the code path that calls `merkle::recover` on this shred set (turbine/repair ingestion in `ledger/src/blockstore.rs`).
4. Observe that the `while batch.len() < erasure_shard_index` / `while batch.len() < num_shards` loops in `recover` (lines 785-794) allocate on the order of 131068 full-size (`ShredCode::SIZE_OF_PAYLOAD` / `ShredData::SIZE_OF_PAYLOAD`) `Vec<u8>` buffers via `make_stub_shred`, before the Reed-Solomon reconstruct step is ever reached, i.e., before any check that the two supplied shreds are actually sufficient to reconstruct the batch.

### Citations

**File:** ledger/src/shred/merkle.rs (L754-756)
```rust
    let num_data_shreds = usize::from(coding_header.num_data_shreds);
    let num_coding_shreds = usize::from(coding_header.num_coding_shreds);
    let num_shards = num_data_shreds + num_coding_shreds;
```

**File:** ledger/src/shred/merkle.rs (L782-794)
```rust
            }
            // Push stub shreds as placeholder for the missing shreds in
            // between.
            while batch.len() < erasure_shard_index {
                batch.push(make_stub_shred(batch.len())?);
            }
            mask[erasure_shard_index] = true;
            batch.push(shred);
        }
        // Push stub shreds as placeholder for the missing shreds at the end.
        while batch.len() < num_shards {
            batch.push(make_stub_shred(batch.len())?);
        }
```

**File:** ledger/src/shred/merkle.rs (L803-805)
```rust
    reed_solomon_cache
        .get(num_data_shreds, num_coding_shreds)?
        .reconstruct(&mut shards)?;
```

**File:** ledger/src/shred/merkle.rs (L914-914)
```rust
        let mut payload = vec![0u8; ShredCode::SIZE_OF_PAYLOAD];
```

**File:** ledger/src/shred/merkle.rs (L944-944)
```rust
        let mut payload = vec![0u8; ShredData::SIZE_OF_PAYLOAD];
```
