### Title
Unguarded Underflow in `get_block_os_output_header` Produces Corrupt `prev_block_number` in OS Output — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`)

---

### Summary

`get_block_os_output_header()` computes `prev_block_number = block_number - 1` with no guard for `block_number == 0`. In Cairo's field arithmetic, `0 - 1` silently wraps to `P - 1` (the field prime minus one, ≈ 2^251), producing a massively incorrect value that is serialized verbatim into the OS output header and forwarded to the L1 StarkNet core contract for chain-continuity verification.

---

### Finding Description

In `os_utils.cairo`, `write_block_number_to_block_hash_mapping` performs an analogous subtraction (`block_number - STORED_BLOCK_HASH_BUFFER`) and **correctly** guards it with `is_nn`: [1](#0-0) 

```cairo
tempvar old_block_number = block_context.block_info_for_execute.block_number -
    STORED_BLOCK_HASH_BUFFER;
let is_old_block_number_non_negative = is_nn(old_block_number);
if (is_old_block_number_non_negative == FALSE) {
    // Not enough blocks in the system - nothing to write.
    return ();
}
```

`get_block_os_output_header`, called in the same block-processing loop, performs the same class of subtraction **without any guard**: [2](#0-1) 

```cairo
tempvar os_output_header = new OsOutputHeader(
    state_update_output=state_update_output,
    prev_block_number=block_context.block_info_for_execute.block_number - 1,  // ← no guard
    new_block_number=block_context.block_info_for_execute.block_number,
    ...
);
```

When `block_number == 0`, `block_number - 1` evaluates to `P - 1` (≈ 3.6 × 10^75) in the field. This value is then serialized directly into the proof output: [3](#0-2) 

```cairo
func serialize_output_header{output_ptr: felt*}(os_output_header: OsOutputHeader*) {
    serialize_word(os_output_header.state_update_output.initial_root);
    serialize_word(os_output_header.state_update_output.final_root);
    serialize_word(os_output_header.prev_block_number);   // ← P-1 written here
    serialize_word(os_output_header.new_block_number);
    ...
```

The `OsOutputHeader.prev_block_number` field is defined as a plain `felt` with no range constraint: [4](#0-3) 

The OS explicitly accounts for early blocks in `write_block_number_to_block_hash_mapping` (guarding against `block_number < STORED_BLOCK_HASH_BUFFER = 10`), confirming that small block numbers including 0 are within the intended operational range of the OS. [5](#0-4) 

---

### Impact Explanation

The `prev_block_number` field in the serialized OS output is consumed by the L1 StarkNet core contract to enforce chain continuity (i.e., that each proven block's declared predecessor matches the last accepted block). When `block_number == 0`:

- `prev_block_number` is emitted as `P - 1`, a value that can never match any legitimate on-chain block number.
- The L1 contract's continuity check fails, making it **impossible to finalize block 0 on L1**.
- Because block 0 is the genesis block, failure to finalize it prevents the chain from ever advancing past its initial state — a **total network shutdown** (High impact).

Additionally, if the L1 contract performs the continuity check as `new_block_number == prev_block_number + 1` in field arithmetic, then `(P - 1) + 1 = P ≡ 0`, which equals `new_block_number = 0`. This could allow the L1 contract to **accept a proof with a corrupted header**, enabling an invalid state transition — a **direct loss of funds** (Critical impact).

---

### Likelihood Explanation

The OS explicitly handles `block_number < 10` in `write_block_number_to_block_hash_mapping`, demonstrating that the OS is designed to be invoked for early blocks including block 0. The absence of a parallel guard in `get_block_os_output_header` is an inconsistency in the same code path, reachable whenever the OS proves the genesis block. No privileged key or malicious operator action is required — a legitimate sequencer processing block 0 triggers the bug.

---

### Recommendation

Apply the same `is_nn` guard pattern already used in `write_block_number_to_block_hash_mapping`:

```cairo
// In get_block_os_output_header:
let block_number = block_context.block_info_for_execute.block_number;
let is_positive = is_nn(block_number - 1);
tempvar prev_block_number = is_positive * (block_number - 1);
// prev_block_number == 0 when block_number == 0 (genesis), else block_number - 1
```

Or add an explicit assertion that `block_number >= 1` if the OS is never intended to process block 0, making the constraint explicit and verifiable.

---

### Proof of Concept

1. Sequencer submits OS input with `block_info.block_number = 0` (genesis block).
2. `get_block_context` stores this in `block_context.block_info_for_execute.block_number`.
3. `execute_blocks` calls `get_block_os_output_header`.
4. Line 140 computes `0 - 1 = P - 1` in field arithmetic — no assertion, no `is_nn` check.
5. `OsOutputHeader.prev_block_number = P - 1` is constructed.
6. `serialize_output_header` writes `P - 1` to the output segment at the `prev_block_number` slot.
7. The L1 StarkNet contract reads `prev_block_number = P - 1`; chain-continuity check fails or (if checked as field equality `prev + 1 == new`) silently passes with `0 == 0`, accepting a corrupt proof. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L52-58)
```text
    tempvar old_block_number = block_context.block_info_for_execute.block_number -
        STORED_BLOCK_HASH_BUFFER;
    let is_old_block_number_non_negative = is_nn(old_block_number);
    if (is_old_block_number_non_negative == FALSE) {
        // Not enough blocks in the system - nothing to write.
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L120-150)
```text
// Returns the OS output header of the given block.
func get_block_os_output_header{poseidon_ptr: PoseidonBuiltin*}(
    block_context: BlockContext*,
    state_update_output: CommitmentUpdate*,
    os_global_context: OsGlobalContext*,
) -> OsOutputHeader* {
    // Calculate the block hash based on the block info and state root.
    // NOTE: both the previous block hash and previous state root are guessed, and the OS
    // does not verify their consistency (unlike the new hash and root).
    // The consumer of the OS output should verify both.
    // TODO(Yoni): verify the consistency of the previous block hash and state root, and remove the
    // state roots from the OS output header.
    let (prev_block_hash, new_block_hash) = get_block_hashes{poseidon_ptr=poseidon_ptr}(
        block_info=block_context.block_info_for_execute, state_root=state_update_output.final_root
    );

    // All blocks inside of a multi block should be off-chain and therefore
    // should not be compressed.
    tempvar os_output_header = new OsOutputHeader(
        state_update_output=state_update_output,
        prev_block_number=block_context.block_info_for_execute.block_number - 1,
        new_block_number=block_context.block_info_for_execute.block_number,
        prev_block_hash=prev_block_hash,
        new_block_hash=new_block_hash,
        os_program_hash=0,
        starknet_os_config_hash=os_global_context.starknet_os_config_hash,
        use_kzg_da=FALSE,
        full_output=TRUE,
    );
    return os_output_header;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L34-49)
```text
struct OsOutputHeader {
    state_update_output: CommitmentUpdate*,
    prev_block_number: felt,
    new_block_number: felt,
    prev_block_hash: felt,
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
    // The hash of the OS program, if the aggregator was used. Zero if the OS was used directly.
    os_program_hash: felt,
    starknet_os_config_hash: felt,
    // Indicates whether to use KZG commitment scheme instead of adding the data-availability to
    // the transaction data.
    use_kzg_da: felt,
    // Indicates whether previous state values are included in the state update information.
    full_output: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L157-173)
```text
func serialize_output_header{output_ptr: felt*}(os_output_header: OsOutputHeader*) {
    // Serialize program output.

    // Serialize roots.
    serialize_word(os_output_header.state_update_output.initial_root);
    serialize_word(os_output_header.state_update_output.final_root);
    serialize_word(os_output_header.prev_block_number);
    serialize_word(os_output_header.new_block_number);
    serialize_word(os_output_header.prev_block_hash);
    serialize_word(os_output_header.new_block_hash);
    serialize_word(os_output_header.os_program_hash);
    serialize_word(os_output_header.starknet_os_config_hash);
    serialize_word(os_output_header.use_kzg_da);
    serialize_word(os_output_header.full_output);

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L65-65)
```text
const STORED_BLOCK_HASH_BUFFER = 10;
```
