### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezal of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` from the calling contract without verifying that the hash corresponds to a previously declared class. An attacker-controlled contract can replace its own class with a non-existent class hash, permanently bricking the contract and freezing any funds held within it.

---

### Finding Description

The `execute_replace_class` function in `syscall_impls.cairo` reads the caller-supplied `class_hash` from the syscall request and immediately writes it into the contract's `StateEntry` in `contract_state_changes`, with no check that the hash exists in the `contract_class_changes` dictionary (i.e., that it was ever declared via a `Declare` transaction).

The code itself contains an explicit acknowledgment of this missing check:

```cairo
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
``` [1](#0-0) 

Contrast this with `execute_declare_transaction`, which enforces that a class can only be declared once and that `compiled_class_hash` is non-zero before writing to `contract_class_changes`:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

The `replace_class` path performs no equivalent existence check against `contract_class_changes`. Any felt value — including `0xdeadbeef` or any other undeclared hash — is accepted as a valid replacement class.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a contract's `class_hash` in `StateEntry` is set to a value that has no corresponding entry in the compiled class facts (i.e., was never declared), any subsequent call to that contract will fail at class lookup time. The contract becomes permanently uncallable. All ERC-20 tokens, NFTs, or native STRK/ETH balances held in that contract's storage are irreversibly frozen, with no recovery path.

This is a state-transition bypass: the OS's business logic assumes that only declared classes can be active on a contract, but `replace_class` bypasses this invariant entirely.

---

### Likelihood Explanation

**High.**

- The `replace_class` syscall is a standard, permissionless operation callable by any contract on itself.
- An attacker deploys a contract, attracts user deposits (e.g., by posing as a vault or bridge), then calls `replace_class` with an arbitrary undeclared hash.
- No privileged role, leaked key, or external dependency is required.
- The attack is a single syscall invocation and is fully deterministic.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, verify that the hash exists as a key in `contract_class_changes` (i.e., it was previously declared). This is exactly what the existing TODO comment calls for:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The fix should perform a `dict_read` on `contract_class_changes` with the requested `class_hash` as the key and assert that the returned `compiled_class_hash` is non-zero, mirroring the invariant enforced by `execute_declare_transaction`.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts deposits and exposes a `drain()` entry point that calls `replace_class(class_hash=0xdeadbeef)`.
2. Users deposit funds into `MaliciousVault`.
3. Attacker calls `drain()`. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`.
4. No validation is performed. `contract_state_changes` is updated: `MaliciousVault`'s `StateEntry.class_hash` is now `0xdeadbeef`.
5. The block is proven and the state root is updated to reflect this change.
6. Any subsequent `call_contract` or `invoke` targeting `MaliciousVault` causes the OS to look up class `0xdeadbeef` in compiled class facts — it does not exist.
7. Execution fails unconditionally. All deposited funds are permanently frozen.

The root cause is exclusively in the production OS file at the cited lines; no test, config, or external dependency is involved. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
