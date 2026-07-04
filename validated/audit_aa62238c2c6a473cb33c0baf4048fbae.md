### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not verify that the new class hash supplied by a contract corresponds to a previously declared class. The missing check is explicitly acknowledged with a `TODO` comment in the code. As a result, any contract can replace its own class hash with an arbitrary undeclared felt value. The OS accepts this state transition and produces a valid proof. The contract is then permanently bricked — all subsequent calls to it will fail because no compiled class exists for the new hash — and any funds held in the contract's storage are permanently frozen.

---

### Finding Description

The `execute_replace_class` function handles the `replace_class` syscall. It reads the requested new class hash from the syscall request, updates the `contract_state_changes` dictionary with the new class hash, and logs the old class hash in the revert log. Critically, it performs **no validation** that the new class hash has been declared (i.e., that it exists in `contract_class_changes` with a corresponding compiled class hash).

The relevant code in `syscall_impls.cairo`:

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

By contrast, `execute_declare_transaction` enforces that a class can only be declared once by asserting `prev_value=0` when writing to `contract_class_changes`:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

There is no cross-check between `contract_state_changes` (which stores each contract's current class hash) and `contract_class_changes` (which stores declared class hashes). The OS accepts any felt value as a valid new class hash in `execute_replace_class`.

The `StateEntry` struct stores `class_hash`, `storage_ptr`, and `nonce`. When `replace_class` is called, only `class_hash` changes; storage and nonce are preserved. This means funds stored in the contract's storage remain in place but become permanently inaccessible once the class hash points to a non-existent class. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:

1. The OS produces a valid proof for the block containing the `replace_class` call (the syscall itself succeeds — it only writes to `contract_state_changes`).
2. The new state root commits to the contract having an undeclared class hash.
3. In all subsequent blocks, any attempt to call the contract will fail at the OS level because no compiled class exists for the new hash. The OS cannot execute the contract's entry points.
4. Any ERC-20 balances, NFTs, or other assets held in the contract's storage are permanently frozen with no recovery path.

This is a direct analog to M-07: just as upgrading a `PaymentEscrow` module without migrating state locks all rental funds, replacing a contract's class with an undeclared hash locks all funds in that contract's storage permanently.

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer:

- A user deploys a contract (e.g., a vault, escrow, or token contract) that holds funds on behalf of other users.
- The contract contains a function (intentionally malicious or accidentally buggy) that calls `replace_class` with an arbitrary felt value that has not been declared.
- The sequencer includes the transaction. The `replace_class` syscall itself succeeds at the OS level — no revert occurs.
- The OS produces a valid proof. The state root is updated to reflect the undeclared class hash.
- All funds in the contract are permanently frozen.

No privileged role is required. Any contract deployer can trigger this. The explicit `TODO` comment confirms the developers are aware the check is missing.

---

### Recommendation

In `execute_replace_class`, add a validation step that verifies the requested `class_hash` exists in `contract_class_changes` (i.e., has been declared in the current or a prior block). Specifically:

- Perform a `dict_read` on `contract_class_changes` for the new `class_hash`.
- Assert that the returned `compiled_class_hash` is non-zero (i.e., the class has been declared).
- Only proceed with the state update if the check passes; otherwise write a failure response.

This mirrors the enforcement already present in `execute_declare_transaction` and closes the gap between what the protocol intends and what the OS currently enforces.

---

### Proof of Concept

1. Deploy a contract `VaultContract` that holds user ERC-20 balances in storage and exposes a function `brick_self()` that calls `replace_class(0xdeadbeef)` — an arbitrary undeclared class hash.
2. A user deposits funds into `VaultContract`.
3. The contract owner calls `brick_self()`.
4. The OS executes `execute_replace_class`: gas is deducted, `contract_state_changes` is updated with `class_hash=0xdeadbeef`, the revert log records the old class hash. **No check is performed against `contract_class_changes`.** The syscall returns success.
5. The block is proven. The new state root commits `VaultContract.class_hash = 0xdeadbeef`.
6. In the next block, any user attempting to withdraw calls `VaultContract.__execute__`. The OS looks up `class_hash=0xdeadbeef` in the compiled class facts bundle — it is not present. The OS cannot execute the entry point. The proof fails for any block that includes a call to `VaultContract`.
7. All user funds in `VaultContract`'s storage are permanently frozen with no recovery mechanism.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```
