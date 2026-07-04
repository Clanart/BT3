### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary felt value as the new class hash without verifying that the hash corresponds to a previously declared class. This is directly analogous to the reported vulnerability's second root cause — reading unverified state (`slot0` tick) without integrity checks — and allows any unprivileged contract to permanently corrupt its own class pointer, freezing all funds held at that address.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

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
    ...
}
```

The `class_hash` value is taken directly from the syscall request — fully attacker-controlled — and written into `contract_state_changes` with no check that it exists in `contract_class_changes` (the dict of declared Sierra classes). The TODO comment at line 898 explicitly acknowledges this missing invariant enforcement. [1](#0-0) 

The `contract_class_changes` dict is the authoritative record of declared classes within a block. The OS never cross-references the new class hash against it before committing the state update. [2](#0-1) 

The state commitment path in `commitment.cairo` also performs no such validation — it hashes whatever `class_hash` is present in the `StateEntry` without checking it is declared: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` field in the global state tree is set to an undeclared hash, every subsequent transaction targeting that contract will fail at class resolution. The contract becomes permanently unexecutable. Any ERC-20 tokens, ETH, or other assets held in that contract's storage are irrecoverably frozen. There is no recovery path: `replace_class` itself requires the contract to be executable to be called again, and the contract can no longer execute.

---

### Likelihood Explanation

**Medium.**

The direct attack surface is any upgradeable contract whose upgrade entry point has insufficient access control (e.g., callable by any address, or protected only by a weak check). An attacker who can invoke such an entry point passes an undeclared felt as the new class hash. The OS provides zero defense. Additionally, a buggy contract that computes a class hash incorrectly (e.g., off-by-one in a hash computation) will silently corrupt itself. The TODO comment in the production code confirms the developers are aware the check is absent.

---

### Recommendation

Before writing the new `StateEntry` in `execute_replace_class`, verify that `class_hash` is present in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero (`UNINITIALIZED_CLASS_HASH` would indicate the class was never declared). This mirrors the existing pattern used in `execute_declare_transaction` where `prev_value=0` enforces uniqueness. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys contract `A` holding user funds (e.g., an ERC-20 vault), with an upgrade function:
   ```
   fn upgrade(new_class_hash: felt252) {
       replace_class_syscall(new_class_hash);  // no access control or validation
   }
   ```
2. Attacker calls `A.upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
3. The OS dispatches to `execute_replace_class`. At line 896, `class_hash = 0xdeadbeef` is read from the request. At line 906, `dict_update` writes `StateEntry { class_hash: 0xdeadbeef, ... }` into `contract_state_changes` with no validation.
4. The block is proven and the state root is updated. Contract `A`'s on-chain class hash is now `0xdeadbeef`.
5. Any subsequent invoke targeting `A` reads `class_hash = 0xdeadbeef` from state, fails to find a compiled class, and the transaction reverts. All funds in `A` are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L190-203)
```text
    let (prev_value) = get_contract_state_hash(
        class_hash=prev_state.class_hash,
        storage_root=initial_contract_state_root,
        nonce=prev_state.nonce,
    );
    assert hashed_state_changes.prev_value = prev_value;
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
