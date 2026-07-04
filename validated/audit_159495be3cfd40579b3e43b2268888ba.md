### Title
Unverified `BlockHeaderCommitments` in `get_block_hashes` Allows Arbitrary Block Hash Manipulation — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo`)

---

### Summary

The `get_block_hashes` function in `block_hash.cairo` sets all block hash inputs — `header_commitments` (containing `transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, `packed_lengths`), `gas_prices_hash`, `starknet_version`, and `previous_block_hash` — exclusively via the hint `GetBlockHashes`, with **no Cairo-level assertions** verifying their correctness. A malicious prover can freely substitute arbitrary values for these fields, producing a `new_block_hash` that does not commit to the actual transactions, events, receipts, or state diff of the block. This `new_block_hash` is then serialized into the OS output and stored on L1 as the canonical block hash.

---

### Finding Description

In `get_block_hashes`, the function signature accepts only `block_info` and `state_root` as constrained inputs. All remaining inputs to `calculate_block_hash` are declared as `local` variables and populated entirely by the hint `GetBlockHashes`:

```cairo
func get_block_hashes{poseidon_ptr: PoseidonBuiltin*}(block_info: BlockInfo*, state_root: felt) -> (
    previous_block_hash: felt, new_block_hash: felt
) {
    alloc_locals;
    local previous_block_hash;
    // Currently, the header commitments and gas prices are not computed by the OS.
    // TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
    local header_commitments: BlockHeaderCommitments*;
    local gas_prices_hash;
    local starknet_version;

    %{ GetBlockHashes %}   // <-- ALL locals set here, no Cairo assertion follows

    let block_hash = calculate_block_hash(
        block_info=block_info,
        header_commitments=header_commitments,
        ...
        previous_block_hash=previous_block_hash,
        starknet_version=starknet_version,
    );

    %{ CheckBlockHashConsistency %}  // <-- also just a hint, not a Cairo assert

    return (previous_block_hash=previous_block_hash, new_block_hash=block_hash);
}
``` [1](#0-0) 

`calculate_block_hash` hashes all five `BlockHeaderCommitments` fields (`transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, `packed_lengths`), `gas_prices_hash`, `starknet_version`, and `previous_block_hash` into the final block hash: [2](#0-1) 

In Cairo, hints are prover-side Python code. They carry **zero proof-system enforcement**. Only `assert` statements in Cairo code constrain the prover. Since no Cairo assertion follows `%{ GetBlockHashes %}`, the prover can assign any value to `header_commitments`, `gas_prices_hash`, `starknet_version`, and `previous_block_hash`. The `%{ CheckBlockHashConsistency %}` hint at line 79 is equally unconstrained — it is also just a hint.

The code's own comment acknowledges this:

> "Currently, the header commitments and gas prices are not computed by the OS. TODO(Yoni, 1/1/2027): compute the header commitments and gas prices." [3](#0-2) 

The resulting `new_block_hash` is then placed into `OsOutputHeader` and serialized to the output segment without any further verification: [4](#0-3) 

The OS output header comment explicitly confirms the OS does not verify `prev_block_hash` consistency either:

> "NOTE: both the previous block hash and previous state root are guessed, and the OS does not verify their consistency (unlike the new hash and root). The consumer of the OS output should verify both." [5](#0-4) 

Both `prev_block_hash` and `new_block_hash` are then serialized to the output segment: [6](#0-5) 

---

### Impact Explanation

**High — Unintended chain split (network partition).**

The `new_block_hash` is the canonical identifier of a block stored on L1. Because `transaction_commitment`, `event_commitment`, `receipt_commitment`, `state_diff_commitment`, and `packed_lengths` are all hint-supplied and unconstrained, a prover can produce a valid STARK proof for a block where the `new_block_hash` does not reflect the actual set of transactions executed. Concretely:

1. **Chain split vector**: Two competing provers processing the same block can each supply different `header_commitments` values. Both proofs are valid under the Cairo constraints (since no assertion checks these fields). They produce two different `new_block_hash` values for the same block. The L1 contract accepts whichever proof arrives first and rejects the second. Nodes that computed the expected block hash independently (e.g., full nodes replaying transactions) will disagree with the L1-stored hash, causing a network partition between nodes that trust L1 and nodes that independently verify.

2. **Commitment integrity loss**: The `transaction_commitment` is the root of the Merkle tree over all transactions in the block. If it is freely settable, the block hash no longer commits to the actual transaction set. This breaks the ability to prove transaction inclusion using the block hash, undermining the integrity of the entire commitment scheme.

---

### Likelihood Explanation

**Medium.** The attack requires a prover that deviates from the honest hint implementation. In StarkNet's current architecture, the sequencer operates the prover. A sequencer that is compromised, malicious, or subject to a software bug in the hint implementation (`GetBlockHashes`) can trigger this without any special privilege beyond the ability to submit proofs — which is the sequencer's normal role. No external attacker action (transaction, message, etc.) is needed to trigger the flaw; it is entirely within the prover's control during normal block production.

---

### Recommendation

Replace the hint-only population of `header_commitments`, `gas_prices_hash`, `starknet_version`, and `previous_block_hash` with Cairo-computed and Cairo-asserted values:

1. **`transaction_commitment`**: Compute it inside the OS from the actual transaction hashes produced during `execute_transactions`, using the same Merkle/Poseidon scheme defined in the protocol spec. Assert the computed value equals the hint-supplied value before using it in `calculate_block_hash`.
2. **`event_commitment`, `receipt_commitment`, `state_diff_commitment`**: Similarly compute from the actual execution outputs and assert equality.
3. **`previous_block_hash`**: Assert it equals the value stored in the block hash contract at `BLOCK_HASH_CONTRACT_ADDRESS` for `block_number - 1`, which is already written by `write_block_number_to_block_hash_mapping`.
4. **`gas_prices_hash` and `starknet_version`**: Derive from the `BlockContext` (which is already constrained) and assert.

The TODO comment at line 61 (`TODO(Yoni, 1/1/2027)`) confirms this is a planned fix; it should be treated as a critical security gap in the interim.

---

### Proof of Concept

**Setup**: A malicious sequencer/prover controls the hint execution environment.

**Step 1**: During proof generation for block N, the prover executes `get_block_hashes`. The hint `GetBlockHashes` is invoked.

**Step 2**: Instead of computing the correct `transaction_commitment = Merkle(tx_hash_1, tx_hash_2, ..., tx_hash_k)`, the prover sets `header_commitments.transaction_commitment = 0` (or any arbitrary felt).

**Step 3**: `calculate_block_hash` computes `new_block_hash = Poseidon(BLOCK_HASH_VERSION, block_number, state_root, sequencer_address, block_timestamp, 0, 0, 0, 0, 0, gas_prices_hash, starknet_version, 0, previous_block_hash)` — a hash over the tampered inputs.

**Step 4**: No Cairo `assert` fires. The proof is valid. The OS outputs this tampered `new_block_hash` via `serialize_output_header`. [7](#0-6) 

**Step 5**: The L1 verifier contract accepts the proof (the STARK is valid) and stores the tampered `new_block_hash` as the canonical hash for block N.

**Step 6**: Any full node that independently computes the block hash from the actual transactions will compute a different value, diverging from L1's canonical hash — a chain split.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L30-49)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L55-82)
```text
func get_block_hashes{poseidon_ptr: PoseidonBuiltin*}(block_info: BlockInfo*, state_root: felt) -> (
    previous_block_hash: felt, new_block_hash: felt
) {
    alloc_locals;
    local previous_block_hash;
    // Currently, the header commitments and gas prices are not computed by the OS.
    // TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
    local header_commitments: BlockHeaderCommitments*;
    local gas_prices_hash;
    // TODO(Yoni): move to global context, and consider enforcing a specific version for the
    // non-virtual OS.
    local starknet_version;

    %{ GetBlockHashes %}

    let block_hash = calculate_block_hash(
        block_info=block_info,
        header_commitments=header_commitments,
        gas_prices_hash=gas_prices_hash,
        state_root=state_root,
        previous_block_hash=previous_block_hash,
        starknet_version=starknet_version,
    );

    %{ CheckBlockHashConsistency %}

    return (previous_block_hash=previous_block_hash, new_block_hash=block_hash);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L126-149)
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
