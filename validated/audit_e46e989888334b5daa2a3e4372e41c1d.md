### Title
Block Hash Does Not Enforce Transaction, Event, Receipt, State-Diff Commitments or Gas Prices — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo`)

---

### Summary

The StarkNet OS computes a block hash that is committed to L1 as the canonical block identifier. The block hash is designed to commit to five critical sub-commitments (`transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, `packed_lengths`) and a `gas_prices_hash`. However, none of these values are computed by the OS. They are all supplied via an unverified prover hint and are never constrained by any Cairo assertion. A prover can therefore produce a valid proof with an arbitrary block hash that misrepresents the actual block contents.

---

### Finding Description

In `block_hash.cairo`, the function `get_block_hashes` is responsible for computing the new block hash. The `BlockHeaderCommitments` struct and `gas_prices_hash` are declared as locals and populated entirely by the hint `%{ GetBlockHashes %}`:

```cairo
// Currently, the header commitments and gas prices are not computed by the OS.
// TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
local header_commitments: BlockHeaderCommitments*;
local gas_prices_hash;

%{ GetBlockHashes %}
```

These hint-supplied values are then passed directly into `calculate_block_hash` without any Cairo-level constraint or assertion verifying them against the actual block data. The `%{ CheckBlockHashConsistency %}` call at line 79 is also a hint — hints are not part of the Cairo proof and impose no constraint on the verifier.

The `BlockHeaderCommitments` struct contains:
- `transaction_commitment` — Merkle root over all transactions in the block
- `event_commitment` — commitment to all emitted events
- `receipt_commitment` — commitment to all transaction receipts
- `state_diff_commitment` — commitment to the state diff
- `packed_lengths` — packed encoding of transaction count, event count, state diff length, and L1 DA mode

All five fields, plus `gas_prices_hash`, are unconstrained by the proof.

A second related gap exists in `os_utils.cairo` at `write_block_number_to_block_hash_mapping`: the `old_block_hash` written into the block hash contract storage is also hint-supplied with no Cairo enforcement:

```cairo
// Currently, the block hash mapping is not enforced by the OS.
// TODO(Yoni, 1/1/2026): output this hash.
local old_block_hash;
%{ GetOldBlockNumberAndHash %}
```

---

### Impact Explanation

The block hash is the canonical on-chain commitment to a block's contents. It is committed to L1 via the OS output header (`new_block_hash` field in `OsOutputHeader`) and is used by L2 nodes as the authoritative reference for a block.

Because `transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, `packed_lengths`, and `gas_prices_hash` are all unconstrained:

1. **Unintended chain split (High):** L2 full nodes that independently verify the block hash by recomputing it from the actual transaction set, events, receipts, and state diff will compute a different hash than the one committed to L1. Nodes that trust the L1-committed hash and nodes that recompute it from block data will disagree on the canonical block hash, causing a network partition.

2. **Direct loss of funds (Critical):** `gas_prices_hash` is unconstrained. A prover can claim arbitrary gas prices in the block hash. Since the block hash is the L1-verified record of the block, misrepresented gas prices corrupt the on-chain record of fee conditions, enabling fee manipulation and potential loss of user funds through incorrect fee accounting.

---

### Likelihood Explanation

The prover controls hint execution. The OS explicitly documents that these fields are not computed (`TODO(Yoni, 1/1/2027)`), confirming this is a known, persistent gap in the protocol. Any proof produced by the current OS — even by an honest prover who makes a mistake in hint logic — will be accepted with unverified commitment fields. The entry path requires no special privilege beyond being the block prover, which is the normal operational role in the StarkNet protocol.

---

### Recommendation

Compute `header_commitments` (all five sub-fields) and `gas_prices_hash` directly in Cairo within `get_block_hashes`, using the actual transaction list, event list, receipt list, state diff, and block context gas prices. Replace the hint-supplied locals with Cairo-computed values and add `assert` statements to constrain them before passing to `calculate_block_hash`. Remove reliance on `%{ GetBlockHashes %}` for these fields.

---

### Proof of Concept

1. Prover constructs a block with transactions `T1, T2, T3` and executes them, producing a valid state root.
2. In the `%{ GetBlockHashes %}` hint, the prover supplies a `transaction_commitment` that is the Merkle root of a different transaction set `T1, T2` (omitting `T3`) and a `gas_prices_hash` reflecting prices 10x higher than actual.
3. `calculate_block_hash` computes a block hash using these fabricated values. No Cairo assertion checks them against the actual executed transactions or block context gas prices.
4. The resulting proof is valid. The OS output commits `new_block_hash` (containing the fabricated commitments) to L1.
5. L1 accepts the proof. The canonical block hash on L1 now misrepresents the transaction set and gas prices.
6. L2 nodes recomputing the block hash from actual block data compute a different value, causing a chain split. Fee accounting based on the fabricated `gas_prices_hash` causes direct fund loss.

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L19-50)
```text
func calculate_block_hash{poseidon_ptr: PoseidonBuiltin*}(
    block_info: BlockInfo*,
    header_commitments: BlockHeaderCommitments*,
    gas_prices_hash: felt,
    state_root: felt,
    previous_block_hash: felt,
    starknet_version: felt,
) -> felt {
    static_assert BlockInfo.SIZE == 3;
    static_assert BlockHeaderCommitments.SIZE == 5;

    let hash_state = hash_init();
    with hash_state {
        hash_update_single(BLOCK_HASH_VERSION);
        hash_update_single(block_info.block_number);
        hash_update_single(state_root);
        hash_update_single(block_info.sequencer_address);
        hash_update_single(block_info.block_timestamp);
        hash_update_single(header_commitments.packed_lengths);
        hash_update_single(header_commitments.state_diff_commitment);
        hash_update_single(header_commitments.transaction_commitment);
        hash_update_single(header_commitments.event_commitment);
        hash_update_single(header_commitments.receipt_commitment);
        hash_update_single(gas_prices_hash);
        hash_update_single(starknet_version);
        hash_update_single(0);
        hash_update_single(previous_block_hash);
    }

    let block_hash = hash_finalize(hash_state=hash_state);
    return block_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L60-68)
```text
    // Currently, the header commitments and gas prices are not computed by the OS.
    // TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
    local header_commitments: BlockHeaderCommitments*;
    local gas_prices_hash;
    // TODO(Yoni): move to global context, and consider enforcing a specific version for the
    // non-virtual OS.
    local starknet_version;

    %{ GetBlockHashes %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L64-67)
```text
    // Currently, the block hash mapping is not enforced by the OS.
    // TODO(Yoni, 1/1/2026): output this hash.
    local old_block_hash;
    %{ GetOldBlockNumberAndHash %}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L126-134)
```text
    // Calculate the block hash based on the block info and state root.
    // NOTE: both the previous block hash and previous state root are guessed, and the OS
    // does not verify their consistency (unlike the new hash and root).
    // The consumer of the OS output should verify both.
    // TODO(Yoni): verify the consistency of the previous block hash and state root, and remove the
    // state roots from the OS output header.
    let (prev_block_hash, new_block_hash) = get_block_hashes{poseidon_ptr=poseidon_ptr}(
        block_info=block_context.block_info_for_execute, state_root=state_update_output.final_root
    );
```
