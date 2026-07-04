### Title
Unverified Hint-Provided Block Hash Written to Mapping Contract Enables Prover to Inject Arbitrary Historical Block Hashes — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`)

---

### Summary

The `write_block_number_to_block_hash_mapping` function in `os_utils.cairo` writes a historical block hash to the `BLOCK_HASH_CONTRACT_ADDRESS` storage using a value (`old_block_hash`) that is sourced entirely from a prover-controlled hint, with **no Cairo-level constraint** verifying its correctness. The code itself acknowledges this: *"Currently, the block hash mapping is not enforced by the OS."* A malicious prover can generate a valid STARK proof that writes an arbitrary value as the block hash for any historical block number. Every contract that subsequently calls the `get_block_hash` syscall will receive this attacker-controlled value, enabling exploitation of any contract that uses historical block hashes for security-critical logic (randomness, replay protection, cross-block commitments), leading to direct loss of funds.

---

### Finding Description

In `write_block_number_to_block_hash_mapping`:

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
``` [1](#0-0) 

The variable `old_block_hash` is declared as a `local` and populated exclusively by the hint `%{ GetOldBlockNumberAndHash %}`. In Cairo's ZK proof model, hints are **prover-supplied witnesses** — they are not constrained by the verifier unless the Cairo program explicitly asserts their correctness. Here, no such assertion exists. The value is written directly into the `BLOCK_HASH_CONTRACT_ADDRESS` storage dict entry at `key=old_block_number` without any check that it equals the actual Poseidon hash of the corresponding historical block.

The `BLOCK_HASH_CONTRACT_ADDRESS` is the dedicated contract (address `0x1`) that stores the block number → block hash mapping: [2](#0-1) 

This mapping is the authoritative source read by the `GET_BLOCK_HASH` syscall, dispatched in `execute_syscalls.cairo`: [3](#0-2) 

The `get_block_hashes` function in `block_hash.cairo` similarly guesses `previous_block_hash` via hint and only runs a hint-level consistency check (`%{ CheckBlockHashConsistency %}`), not a Cairo assertion: [4](#0-3) 

The `get_block_os_output_header` function in `os_utils.cairo` explicitly documents that neither the previous block hash nor the previous state root is verified by the OS: [5](#0-4) 

And the `OsOutputHeader` struct comment in `output.cairo` confirms: *"Currently, the block hash is not enforced by the OS."* [6](#0-5) 

**Analogy to the Chainlink finding:** Just as `latestAnswer()` returned a value with no staleness or completeness check — allowing stale/zero prices to flow into protocol logic — `old_block_hash` is accepted from an external (hint) source with no cryptographic check, allowing a fabricated value to flow into every contract that queries historical block hashes.

---

### Impact Explanation

**Critical — Direct loss of funds.**

Any on-chain contract that calls `get_block_hash(block_number)` and uses the result for a security-sensitive decision receives a prover-controlled value. Concrete scenarios:

- **Commit-reveal / randomness schemes**: A contract that uses `get_block_hash` as a source of unpredictability (e.g., lottery, NFT reveal, gaming) can be manipulated by the prover to produce a predetermined "random" output, allowing the prover to guarantee winning outcomes and drain prize pools.
- **Cross-block replay protection**: A contract that stores a commitment keyed to a block hash and later verifies it against `get_block_hash` can be bypassed if the prover injects a hash matching the attacker's pre-image.
- **Oracle / bridge verification**: Any bridge or oracle that anchors L2 state to a historical block hash for fraud-proof windows receives a fabricated anchor, enabling double-spend or invalid withdrawal proofs.

In all cases the attacker extracts funds from victim contracts in a single provable block, with no recovery path once the proof is accepted on L1.

---

### Likelihood Explanation

The prover in StarkNet's current architecture is the sequencer. The Cairo OS program is the **soundness boundary** of the ZK proof system — its purpose is to constrain the prover so that even a malicious prover cannot produce a valid proof with incorrect state. A missing Cairo-level constraint is a **soundness flaw**, not merely a sequencer-behavior risk. The prover can exploit this without any key compromise, Sybil attack, or external dependency — only by supplying a crafted hint value, which is entirely within the prover's normal operational capability. The TODO comment (`TODO(Yoni, 1/1/2026): output this hash`) confirms the developers are aware the constraint is absent.

---

### Recommendation

Replace the unconstrained hint-provided `old_block_hash` with a Cairo-enforced commitment. Concretely:

1. **Output the old block hash in the OS output** (as the TODO indicates) so the L1 verifier can check it against the previously accepted block hash stored on L1.
2. **Add a Cairo assertion** that `old_block_hash` equals the value committed in the previous block's OS output header (i.e., chain the `new_block_hash` of block `N` as the enforced `old_block_hash` when processing block `N + STORED_BLOCK_HASH_BUFFER`).
3. Until the full fix is in place, at minimum add a range/non-zero check on `old_block_hash` to prevent trivially invalid values (analogous to the Chainlink recommendation of `require(price != 0)`).

---

### Proof of Concept

1. Prover constructs a block at height `N` where `STORED_BLOCK_HASH_BUFFER = 10`, so `old_block_number = N - 10`.
2. In the hint `%{ GetOldBlockNumberAndHash %}`, the prover returns `old_block_hash = H_fake` (any arbitrary felt value chosen to match a pre-image the attacker controls).
3. The Cairo OS writes `DictAccess(key=N-10, prev_value=0, new_value=H_fake)` to `BLOCK_HASH_CONTRACT_ADDRESS` storage — **no Cairo assertion fires** because none exists.
4. The state update is committed; the STARK proof is valid and accepted by the L1 verifier.
5. A victim lottery contract calls `get_block_hash(N-10)` → receives `H_fake`.
6. The attacker, who chose `H_fake` to match their pre-committed ticket, claims the prize → **direct loss of funds**. [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L46-85)
```text
// Writes the hash of the (current_block_number - buffer) block under its block number in the
// dedicated contract state, where buffer=STORED_BLOCK_HASH_BUFFER.
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
}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L58-59)
```text
// This contract stores the block number -> block hash mapping.
const BLOCK_HASH_CONTRACT_ADDRESS = 0x1;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L175-183)
```text
    if (selector == GET_BLOCK_HASH_SELECTOR) {
        execute_get_block_hash(block_context=block_context);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L58-81)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L39-40)
```text
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
```
