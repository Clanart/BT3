### Title
Missing Validation of `class_hash` Argument in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS program accepts an arbitrary `class_hash` value from a user-controlled syscall request without validating that the hash corresponds to a declared contract class. An unprivileged contract caller can supply `0` or any undeclared felt value as the new class hash. The OS will commit this invalid class hash to the contract's state entry. All subsequent calls to that contract will fail irrecoverably because no compiled class exists for the hash, permanently freezing any funds held in the contract's storage.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `class_hash` directly from the user-supplied syscall request and writes it into `contract_state_changes` with no validation:

```cairo
func execute_replace_class{...}(contract_address: felt) {
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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    ...
}
```

The developer-acknowledged TODO at line 898 explicitly states the missing check. There is no `assert_not_zero(class_hash)` and no lookup into `contract_class_changes` or `compiled_class_facts` to confirm the hash is declared. The revert log records the *old* class hash for rollback, but only if the enclosing transaction reverts — the `replace_class` syscall itself always succeeds, so the bad hash is committed permanently on a successful transaction. [1](#0-0) 

Compare with the analogous pattern in `deploy_contract.cairo`, which *does* validate the target address:

```cairo
assert_not_zero(
    (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * ...
);
``` [2](#0-1) 

No equivalent guard exists in `execute_replace_class`.

---

### Impact Explanation

After a successful `replace_class` call with an invalid `class_hash` (e.g., `0` or any undeclared felt):

1. The contract's `StateEntry.class_hash` is permanently set to the invalid value in the committed state.
2. Every subsequent call to that contract causes the OS to look up the class hash in `compiled_class_facts` — a lookup that will always fail because no compiled class exists for the invalid hash.
3. The contract becomes permanently unexecutable. Any ERC-20 tokens, ETH, or other assets stored in the contract's storage slots are irrecoverably locked.

This matches the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly documented StarkNet syscall callable by any contract. No privileged role is required. A user who:

- Deploys a contract holding funds (e.g., a multisig, vault, or token contract), or
- Exploits a reentrancy or logic bug in an existing contract to trigger `replace_class`,

can supply `class_hash = 0` or any random felt. The OS will accept and commit it. The attack requires only a single successful transaction and is irreversible.

---

### Recommendation

Add an explicit validation inside `execute_replace_class` before updating state:

1. **Assert non-zero**: `assert_not_zero(class_hash)` — mirrors the pattern used in `deploy_contract.cairo` for address validation.
2. **Assert declared**: Verify that `class_hash` exists in `contract_class_changes` (or the global compiled class facts bundle) before committing the state update. The existing TODO at line 898 already identifies this requirement.

The fix is directly analogous to the UniswapV3 recommendation: validate the critical argument (`class_hash` here, `_owner` there) before committing it to persistent state.

---

### Proof of Concept

1. Attacker deploys a contract `VaultContract` holding 1000 STRK in its storage.
2. Attacker calls `VaultContract` with a transaction that internally invokes the `replace_class` syscall with `class_hash = 0`.
3. `execute_replace_class` in the OS reads `request.class_hash = 0`, skips all validation (no check exists), and calls `dict_update` to set `VaultContract`'s `StateEntry.class_hash = 0`.
4. The transaction succeeds; the state is committed with `class_hash = 0`.
5. Any subsequent call to `VaultContract` — including withdrawal attempts — causes the OS to look up compiled class `0`, which does not exist in `compiled_class_facts`.
6. All calls revert permanently. The 1000 STRK are frozen forever. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-49)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
```
