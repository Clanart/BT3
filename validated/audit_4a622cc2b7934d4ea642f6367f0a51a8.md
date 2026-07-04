### Title
Unverified `old_block_hash` Written to Block Hash Contract Storage Allows Malicious Prover to Corrupt Historical Block Hashes — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`)

---

### Summary

In `write_block_number_to_block_hash_mapping`, the `old_block_hash` value written into the block hash contract's storage is sourced **exclusively from an unverified hint** with no Cairo-level assertion constraining it. The OS itself acknowledges this gap with an explicit comment. A malicious prover can supply any arbitrary felt as the historical block hash for any block number, corrupting the on-chain mapping that contracts query via the `get_block_hash` syscall, leading to direct loss of funds for contracts that rely on block hash integrity.

---

### Finding Description

In `os_utils.cairo`, the function `write_block_number_to_block_hash_mapping` is called during block pre-processing to record the hash of block `(current_block_number - STORED_BLOCK_HASH_BUFFER)` into the dedicated block hash contract storage: [1](#0-0) 

```cairo
// Currently, the block hash mapping is not enforced by the OS.
// TODO(Yoni, 1/1/2026): output this hash.
local old_block_hash;
%{ GetOldBlockNumberAndHash %}

// Update mapping.
assert state_entry.class_hash = 0;
assert state_entry.nonce = 0;
tempvar storage_ptr = state_entry.storage_ptr;
assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
```

The `old_block_hash` is a `local` variable populated **only** by the hint `%{ GetOldBlockNumberAndHash %}`. There is **no Cairo `assert` statement** that constrains `old_block_hash` to equal the actual Poseidon hash of block `old_block_number`. In the Cairo/STARK proof model, hints are prover-supplied and are not part of the verifiable constraint system — only Cairo `assert` statements create proof constraints. The comment explicitly confirms: *"Currently, the block hash mapping is not enforced by the OS."*

This is compounded by a parallel gap in `block_hash.cairo`, where `previous_block_hash`, `header_commitments`, `gas_prices_hash`, and `starknet_version` are all guessed via hints with no Cairo assertions: [2](#0-1) 

The `%{ CheckBlockHashConsistency %}` at line 79 is itself a hint — it produces no proof constraint. The `output.cairo` struct comment reinforces this: [3](#0-2) 

```cairo
// Currently, the block hash is not enforced by the OS.
new_block_hash: felt,
```

The result is that the entire block hash mapping written into the block hash contract storage — which is the authoritative source for the `get_block_hash` syscall — is unconstrained by the proof.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The block hash contract storage is the canonical source of historical block hashes for all StarkNet contracts. Contracts that use the `get_block_hash` syscall for security-critical decisions (e.g., commit-reveal randomness schemes, fraud proof verification, cross-chain bridge attestations, or time-locked vault logic) will receive the prover-supplied arbitrary value instead of the true block hash. A malicious prover can:

1. Write `old_block_hash = 0` (or any chosen value) for a target block number.
2. Any contract that calls `get_block_hash(target_block_number)` and uses the result to gate fund withdrawals, verify proofs, or generate randomness will operate on a forged value.
3. The L1 verifier accepts the STARK proof because the Cairo constraints are satisfied — the wrong hash is correctly committed into the state trie, and the state root is valid.

There is no L1-side check that the block hash contract storage values match actual block hashes, because the L1 verifier only checks the state root, not the semantic correctness of individual storage slots.

---

### Likelihood Explanation

The StarkNet sequencer is currently centralized and acts as the sole prover. It provides all hints, including `%{ GetOldBlockNumberAndHash %}`. A compromised or malicious sequencer can exploit this gap without any external precondition — no key theft, no Sybil attack, no network-level interference. The exploit is a single-step hint substitution that produces a valid STARK proof. The explicit TODO comment (`TODO(Yoni, 1/1/2026): output this hash`) confirms the developers are aware the enforcement is missing and is deferred, meaning the window of exposure is active in the current codebase.

---

### Recommendation

Add a Cairo-level assertion that constrains `old_block_hash` to equal the verifiably computed hash of block `old_block_number`. Concretely:

1. Include the `(old_block_number, old_block_hash)` pair in the OS output segment so it is committed to the proof.
2. Verify on L1 (or within the OS via a recursive check) that the committed hash matches the previously accepted block hash for that block number.
3. Remove the hint-only path and replace it with a Cairo `assert` after computing or receiving the hash through a verified channel.

This is already tracked as `TODO(Yoni, 1/1/2026): output this hash` at line 65 of `os_utils.cairo`.

---

### Proof of Concept

**Setup:** A DeFi contract `VaultContract` uses `get_block_hash(N - STORED_BLOCK_HASH_BUFFER)` as a commit-reveal randomness seed to determine withdrawal eligibility.

**Attack:**

1. The malicious sequencer, when processing block `N`, calls `write_block_number_to_block_hash_mapping`.
2. In the hint `%{ GetOldBlockNumberAndHash %}`, it sets `old_block_hash = attacker_chosen_value` instead of the real hash of block `N - STORED_BLOCK_HASH_BUFFER`.
3. The Cairo code at line 73 writes `DictAccess(key=old_block_number, prev_value=0, new_value=attacker_chosen_value)` into the block hash contract storage — no assertion fails.
4. The STARK proof is generated and verified on L1. The proof is valid because all Cairo constraints are satisfied.
5. `VaultContract` calls `get_block_hash(N - STORED_BLOCK_HASH_BUFFER)` and receives `attacker_chosen_value`.
6. The attacker, who pre-computed `attacker_chosen_value` to satisfy the vault's randomness check, drains the vault.

The root cause — `old_block_hash` is a hint-only `local` with no constraining `assert` — is at: [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L48-84)
```text
func write_block_number_to_block_hash_mapping{range_check_ptr, contract_state_changes: DictAccess*}(
    block_context: BlockContext*
) {
    alloc_locals;
    tempvar old_block_number = block_context.block_info_for_execute.block_number -
        STORED_BLOCK_HASH_BUFFER;
    let is_old_block_number_non_negative = is_nn(old_block_number);
    if (is_old_block_number_non_negative == FALSE) {
        // Not enough blocks in the system - nothing to write.
        return ();
    }

    // Fetch the (block number -> block hash) mapping contract state.
    local state_entry: StateEntry*;
    %{ GetBlockHashMapping %}

    // Currently, the block hash mapping is not enforced by the OS.
    // TODO(Yoni, 1/1/2026): output this hash.
    local old_block_hash;
    %{ GetOldBlockNumberAndHash %}

    // Update mapping.
    assert state_entry.class_hash = 0;
    assert state_entry.nonce = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
    let storage_ptr = storage_ptr + DictAccess.SIZE;
    %{ WriteOldBlockToStorage %}

    // Update contract state.
    tempvar new_state_entry = new StateEntry(class_hash=0, storage_ptr=storage_ptr, nonce=0);
    dict_update{dict_ptr=contract_state_changes}(
        key=BLOCK_HASH_CONTRACT_ADDRESS,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L59-79)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L39-40)
```text
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
```
