### Title
Missing Declared-Class Validation in `replace_class` Syscall Allows Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program accepts an arbitrary class hash without verifying that the hash corresponds to a declared contract class. Any contract can invoke the `replace_class` syscall with an undeclared or fabricated class hash, permanently rendering itself unexecutable and freezing any funds it holds. The OS proof accepts this state transition as valid, making it irreversible on-chain.

---

### Finding Description

The `execute_replace_class` function processes the `replace_class` syscall by reading the requested class hash from the syscall pointer and directly writing it into `contract_state_changes` without any check that the hash exists in the set of declared classes:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    ...
}
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges the missing check. By contrast, `execute_declare_transaction` correctly validates that a class hash is the output of a proper Sierra class hash computation before registering it in `contract_class_changes`:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
``` [2](#0-1) 

No equivalent validation exists in `execute_replace_class`. The syscall is dispatched unconditionally from `execute_syscalls` whenever the selector matches `REPLACE_CLASS_SELECTOR`: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every future call to that contract will fail at the class-lookup stage (no compiled class exists for that hash). The state transition is committed to the L1 verifier via a valid STARK proof, making it irreversible. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. Because the OS proof is the authoritative record accepted by the L1 verifier, no off-chain remediation can undo the committed state.

---

### Likelihood Explanation

The attack path is directly reachable by any unprivileged contract deployer:

1. Deploy a contract (permissionless on StarkNet).
2. From within that contract's execution, issue a `replace_class` syscall with an arbitrary felt value as the class hash (e.g., `1` or any value with no corresponding declared class).
3. The OS processes the syscall, writes the invalid class hash into `contract_state_changes`, and includes the result in a valid proof.
4. The L1 verifier accepts the proof; the contract is permanently broken.

No privileged role, leaked key, or external dependency is required. The only prerequisite is that the contract holds funds worth targeting — achievable by advertising the contract as a legitimate DeFi vault before triggering the replacement.

---

### Recommendation

Before updating `contract_state_changes` in `execute_replace_class`, verify that the requested `class_hash` exists in `contract_class_changes` (for classes declared in the current block) or in the pre-existing committed state. This mirrors the validation already enforced in `execute_declare_transaction`. Specifically:

- Perform a `dict_read` on `contract_class_changes` for the requested `class_hash` and assert the result is non-zero (i.e., a compiled class hash has been registered).
- If the class was declared in a prior block, verify its existence against the committed state trie.

Remove the TODO comment only after this check is implemented and tested.

---

### Proof of Concept

```cairo
// Attacker's malicious contract (pseudocode):
@external
func drain_and_freeze() {
    // 1. Transfer all held tokens to attacker's EOA (optional, for fund theft variant).
    // 2. Call replace_class with a garbage hash.
    replace_class(class_hash=0xdeadbeef);  // 0xdeadbeef is not a declared class.
    // Contract is now permanently unexecutable.
    // All remaining storage (including user balances) is frozen.
}
```

The OS will process this via `execute_replace_class` at: [4](#0-3) 

The resulting `contract_state_changes` entry with the invalid class hash is serialized into the OS output and committed to L1 via `serialize_os_output`: [5](#0-4) 

The proof is valid; the freeze is permanent.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L78-108)
```text
func serialize_os_output{
    output_ptr: felt*, range_check_ptr, ec_op_ptr: EcOpBuiltin*, poseidon_ptr: PoseidonBuiltin*
}(os_output: OsOutput*, replace_keys_with_aliases: felt, n_public_keys: felt, public_keys: felt*) {
    alloc_locals;

    local use_kzg_da = os_output.header.use_kzg_da;
    local full_output = os_output.header.full_output;
    let compress_state_updates = 1 - full_output;

    // Compute the data availability segment.
    local state_updates_start: felt*;
    let state_updates_ptr = state_updates_start;
    %{ SetStateUpdatesStart %}
    local squashed_os_state_update: SquashedOsStateUpdate* = os_output.squashed_os_state_update;
    with state_updates_ptr {
        // Output the contract state diff.
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
