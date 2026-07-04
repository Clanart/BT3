### Title
Redundant Double Serialization of Full Contract State Diff via `get_full_contract_state_diff` Enables OS Step Exhaustion — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo`)

---

### Summary

`get_full_contract_state_diff` (which internally calls `serialize_full_contract_state_diff`) is invoked twice with identical arguments during every block that uses alias compression: once inside `allocate_aliases` and once inside `replace_aliases_and_serialize_full_contract_state_diff`. Combined with additional squash passes in `state.cairo`, the OS performs at least four to five full linear iterations over the entire block's state diff. Because L2 gas pricing is based on execution cost rather than OS-program step overhead, an unprivileged sender can craft a block whose state diff is large enough to push the OS past the prover's step limit, preventing proof generation and halting the network.

---

### Finding Description

**Two separate pipelines over the same data**

In `aliases.cairo`, `allocate_aliases` calls `get_full_contract_state_diff` at line 167: [1](#0-0) 

`get_full_contract_state_diff` allocates a fresh buffer and calls `serialize_full_contract_state_diff`, which recurses over every contract and, for each contract, recurses over every storage update via `serialize_da_changes`: [2](#0-1) [3](#0-2) 

Later, `replace_aliases_and_serialize_full_contract_state_diff` calls `get_full_contract_state_diff` a **second time** with the same `n_contracts` and `contract_state_changes` arguments: [4](#0-3) 

The result of the first call is not retained or passed to the second function; instead the entire serialization is recomputed from scratch. This is the direct analog of the "two separate pipelines" identified in the external report.

**Full pass count per block**

Tracing the call chain in `state.cairo` and `output.cairo`:

1. `squash_state_changes` — first linear pass over all state changes. [5](#0-4) 

2. `allocate_aliases` → `get_full_contract_state_diff` → `serialize_full_contract_state_diff` — second pass. [6](#0-5) 

3. `squash_dict` (outer contract dict re-sorted after alias contract insertion) — third pass. [7](#0-6) 

4. `output_contract_state` → `replace_aliases_and_serialize_full_contract_state_diff` → `get_full_contract_state_diff` → `serialize_full_contract_state_diff` — fourth pass (the redundant one). [8](#0-7) 

5. `replace_contract_state_diff` — fifth pass over the serialized diff. [9](#0-8) 

The total OS step cost for alias-related processing is therefore proportional to `≥5 × (sum of all storage updates across all contracts in the block)`. The redundant fourth pass alone doubles the cost of the most expensive serialization step.

---

### Impact Explanation

The StarkNet OS runs inside a STARK prover with a fixed maximum step budget. If the OS program requires more steps than the prover's budget, no valid proof can be generated for the block. A block that cannot be proven cannot be finalized, and the sequencer must either discard it or stall. Either outcome means **the network cannot confirm new transactions** — a total network shutdown matching the "High: Network not being able to confirm new transactions" impact category.

---

### Likelihood Explanation

Any unprivileged transaction sender can write to unique storage slots. A single `STORAGE_WRITE` syscall to a previously-unseen key creates one new alias entry and one new state-diff entry. An attacker who submits transactions that collectively touch `K` unique storage keys forces the OS to perform `≥5K` serialization steps for alias processing alone, on top of the normal execution cost. Because L2 gas is priced against execution (Sierra gas), not OS-program steps, the attacker can reach the block's gas ceiling while the OS step count is still growing. The attack requires only normal transaction submission rights and enough funds to pay gas — no privileged access is needed.

---

### Recommendation

1. **Merge the two pipelines.** Compute `get_full_contract_state_diff` exactly once and pass the result to both `allocate_aliases_for_contract_state_diff` and `replace_contract_state_diff`. This eliminates the redundant fourth pass and halves the alias-phase step cost.

2. **Account for OS overhead in gas pricing.** Ensure that the per-storage-write gas cost includes a factor for the multiple OS-level passes (squash, alias allocation, alias replacement, commitment), so that a block at the gas ceiling cannot exceed the prover's step budget.

3. **Consider merging alias allocation and replacement into a single recursive pass**, analogous to the external report's recommendation to remove the signals array and aggregate directly.

---

### Proof of Concept

1. Attacker deploys a contract whose `execute` entry point accepts an array of `(key, value)` pairs and issues one `STORAGE_WRITE` per pair.
2. Attacker submits enough invoke transactions to fill a block, each writing to `M` previously-unseen storage keys (maximising unique keys within the L2 gas budget).
3. The OS processes the block. During `state_update` with `should_allocate_aliases = TRUE`:
   - `serialize_full_contract_state_diff` is called in `allocate_aliases` (pass 2).
   - `serialize_full_contract_state_diff` is called again in `replace_aliases_and_serialize_full_contract_state_diff` (pass 4).
4. Total OS steps for alias serialization ≈ `2 × M × (steps per storage entry)`, on top of the three squash passes.
5. If `M` is chosen so that the block is at the L2 gas ceiling but the OS step count exceeds the prover budget, proof generation fails, the block cannot be finalized, and the network halts.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L162-170)
```text
func allocate_aliases{aliases_storage_updates: DictAccess*, range_check_ptr}(
    n_contracts: felt, contract_state_changes: DictAccess*
) {
    alloc_locals;
    // Compute the full contract state diff.
    let contract_state_diff = get_full_contract_state_diff(
        n_contracts=n_contracts, contract_state_changes=contract_state_changes
    );

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L251-287)
```text
func replace_aliases_and_serialize_full_contract_state_diff{range_check_ptr, res: felt*}(
    n_contracts: felt, contract_state_changes: DictAccess*
) {
    alloc_locals;

    // Compute the full contract state diff.
    let contract_state_diff = get_full_contract_state_diff(
        n_contracts=n_contracts, contract_state_changes=contract_state_changes
    );

    // Extract the final aliases - all aliases that were accessed during the execution
    // (both existing and newly allocated - see `allocate_aliases` documentation).
    static_assert DictAccess.key == 0;
    let (aliases_entry: DictAccess*) = find_element(
        array_ptr=contract_state_changes,
        elm_size=DictAccess.SIZE,
        n_elms=n_contracts,
        key=ALIAS_CONTRACT_ADDRESS,
    );

    let prev_aliases_state_entry = cast(aliases_entry.prev_value, StateEntry*);
    let new_aliases_state_entry = cast(aliases_entry.new_value, StateEntry*);
    let n_aliases = (new_aliases_state_entry.storage_ptr - prev_aliases_state_entry.storage_ptr) /
        DictAccess.SIZE;
    let aliases_ptr = cast(prev_aliases_state_entry.storage_ptr, DictAccess*);

    // Copy the number of modified contracts.
    tempvar n_modified_contracts = contract_state_diff[0];
    assert res[0] = n_modified_contracts;
    let res = &res[1];
    // Write the contract state diff with replaced aliases.
    return replace_contract_state_diff(
        aliases=Aliases(len=n_aliases, ptr=aliases_ptr),
        n_contracts=n_modified_contracts,
        contract_state_diff=&contract_state_diff[1],
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L290-337)
```text
func replace_contract_state_diff{range_check_ptr, res: felt*}(
    aliases: Aliases, n_contracts: felt, contract_state_diff: felt*
) {
    if (n_contracts == 0) {
        return ();
    }
    alloc_locals;

    let contract_header = cast(contract_state_diff, FullContractHeader*);
    local contract_address = contract_header.address;
    local storage_diff_start: FullStateUpdateEntry* = cast(
        &contract_state_diff[FullContractHeader.SIZE], FullStateUpdateEntry*
    );
    local n_storage_diffs = contract_header.n_storage_diffs;
    local storage_diff_end: FullStateUpdateEntry* = &storage_diff_start[n_storage_diffs];
    let skip_contract = should_skip_contract(contract_address=contract_address);
    if (skip_contract != FALSE) {
        // No aliases for this contract - copy the diff and continue.
        tempvar diff_len = cast(storage_diff_end, felt*) - contract_state_diff;
        memcpy(dst=res, src=contract_state_diff, len=diff_len);
        let res = &res[diff_len];
        return replace_contract_state_diff(
            aliases=aliases, n_contracts=n_contracts - 1, contract_state_diff=storage_diff_end
        );
    }

    // Replace the contract address.
    let address_alias = get_alias(aliases=aliases, key=contract_address);
    // Write the header.
    let replaced_contract_header = cast(res, FullContractHeader*);
    assert [replaced_contract_header] = FullContractHeader(
        address=address_alias,
        prev_nonce=contract_header.prev_nonce,
        new_nonce=contract_header.new_nonce,
        prev_class_hash=contract_header.prev_class_hash,
        new_class_hash=contract_header.new_class_hash,
        n_storage_diffs=n_storage_diffs,
    );
    let res = &res[FullContractHeader.SIZE];

    // Replace the storage diff.
    replace_storage_diff(
        aliases=aliases, storage_diff_start=storage_diff_start, storage_diff_end=storage_diff_end
    );
    return replace_contract_state_diff(
        aliases=aliases, n_contracts=n_contracts - 1, contract_state_diff=storage_diff_end
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L416-428)
```text
func get_full_contract_state_diff{range_check_ptr}(
    n_contracts: felt, contract_state_changes: DictAccess*
) -> felt* {
    alloc_locals;
    let (local contract_state_diff_start: felt*) = alloc();
    let res = contract_state_diff_start;
    with res {
        serialize_full_contract_state_diff(
            n_contracts=n_contracts, contract_state_changes=contract_state_changes
        );
    }
    return contract_state_diff_start;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/output.cairo (L305-349)
```text
func serialize_full_contract_state_diff_inner{range_check_ptr, res: felt*, n_modified_contracts}(
    n_contracts: felt, state_changes: DictAccess*
) {
    if (n_contracts == 0) {
        return ();
    }
    alloc_locals;

    local prev_state: StateEntry* = cast(state_changes.prev_value, StateEntry*);
    local new_state: StateEntry* = cast(state_changes.new_value, StateEntry*);

    local storage_dict_start: DictAccess* = prev_state.storage_ptr;
    let storage_dict_end: DictAccess* = new_state.storage_ptr;
    local n_updates = (storage_dict_end - storage_dict_start) / DictAccess.SIZE;

    // Write the full storage diff.
    let storage_diff_ptr: felt* = &res[FullContractHeader.SIZE];
    let n_storage_diffs = serialize_da_changes{state_updates_ptr=storage_diff_ptr}(
        update_ptr=storage_dict_start, n_updates=n_updates, full_output=1
    );
    if (n_storage_diffs == 0 and prev_state.nonce == new_state.nonce and
        prev_state.class_hash == new_state.class_hash) {
        // There are no updates for this contract.
        return serialize_full_contract_state_diff_inner(
            n_contracts=n_contracts - 1, state_changes=&state_changes[1]
        );
    }

    // Write the full contract header.
    let contract_header = cast(res, FullContractHeader*);
    assert [contract_header] = FullContractHeader(
        address=state_changes.key,
        prev_nonce=prev_state.nonce,
        new_nonce=new_state.nonce,
        prev_class_hash=prev_state.class_hash,
        new_class_hash=new_state.class_hash,
        n_storage_diffs=n_storage_diffs,
    );

    let res = cast(storage_diff_ptr, felt*);
    let n_modified_contracts = n_modified_contracts + 1;
    return serialize_full_contract_state_diff_inner(
        n_contracts=n_contracts - 1, state_changes=&state_changes[1]
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L126-129)
```text
    let (n_contract_state_changes, squashed_contract_state_dict) = squash_state_changes(
        contract_state_changes_start=contract_state_changes_start,
        contract_state_changes_end=contract_state_changes_end,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L143-147)
```text
        allocate_aliases(
            n_contracts=n_contract_state_changes,
            contract_state_changes=squashed_contract_state_dict,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L194-198)
```text
    let (final_squashed_contract_state_changes_end) = squash_dict(
        dict_accesses=squashed_contract_state_dict,
        dict_accesses_end=squashed_contract_state_dict_end,
        squashed_dict=final_squashed_contract_state_changes_start,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L94-107)
```text
        output_contract_state(
            contract_state_changes_start=squashed_os_state_update.contract_state_changes,
            n_contract_state_changes=squashed_os_state_update.n_contract_state_changes,
            replace_keys_with_aliases=replace_keys_with_aliases,
            full_output=full_output,
        );

        // Output the contract class diff.
        output_contract_class_da_changes(
            update_ptr=squashed_os_state_update.contract_class_changes,
            n_updates=squashed_os_state_update.n_class_updates,
            full_output=full_output,
        );
    }
```
