### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared contract class. The OS unconditionally writes the caller-supplied class hash into `contract_state_changes` and commits it to the global state tree. A contract that calls `replace_class` with an undeclared hash — whether due to a bug or attacker-controlled input — will have its class permanently set to a non-existent class, making it permanently non-executable and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), after deducting gas, the OS reads `request.class_hash` directly from the syscall request and writes it into `contract_state_changes` without any cross-check against `contract_class_changes`:

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
```

The `contract_class_changes` dict is the authoritative record of declared classes. In `execute_declare_transaction` (`transaction_impls.cairo`, lines 814–819), a class is registered there with `prev_value=0` enforcing uniqueness:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

`execute_replace_class` has access to `contract_state_changes` but **not** `contract_class_changes` in its implicit arguments. It therefore cannot and does not perform the cross-check. The TODO comment at line 898 explicitly acknowledges this missing validation.

Once the undeclared class hash is committed into `contract_state_changes`, it flows through `hash_contract_state_changes` → `compute_contract_state_commitment` → `calculate_global_state_root` (`commitment.cairo`) and is permanently embedded in the proven global state root. There is no rollback path after proof finalization.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract whose class hash is replaced with an undeclared value becomes permanently non-executable: the execution engine will find no bytecode for that class hash and every future call to the contract will fail. All ERC-20 tokens, ETH, or other assets held in the contract's storage are irrecoverably frozen. Because the state transition is accepted by the OS and committed to the proven state root, the freeze is irreversible at the protocol level.

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly documented StarkNet syscall callable by any contract. Contracts that accept a class hash as calldata and forward it to `replace_class` (e.g., generic proxy or upgrade-manager patterns) are a realistic and common design. An attacker who can invoke such a contract with an arbitrary felt value as the target class hash can trigger the freeze. No privileged role, leaked key, or operator cooperation is required — only the ability to send a transaction to a vulnerable contract.

---

### Recommendation

Add `contract_class_changes` as an implicit argument to `execute_replace_class` and assert that the requested class hash has a non-zero entry in that dict before updating `contract_state_changes`:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,   // <-- add
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // Verify the class has been declared.
    let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
    if (compiled_class_hash == 0) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }
    ...
}
```

This mirrors the enforcement already present in `execute_declare_transaction`, where `prev_value=0` guarantees a class is registered before use.

---

### Proof of Concept

1. Declare class `A` (valid, registered in `contract_class_changes`).
2. Deploy contract `C` using class `A`; `C` holds user funds and exposes:
   ```
   fn upgrade(new_class_hash: felt252) { replace_class_syscall(new_class_hash); }
   ```
3. Attacker calls `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
4. `execute_replace_class` reads `class_hash = 0xdeadbeef`, skips the missing validation, and writes `StateEntry { class_hash: 0xdeadbeef, ... }` into `contract_state_changes`.
5. The OS commits this to the state tree. The block is proven and finalized on L1.
6. All subsequent calls to `C` fail — no bytecode exists for `0xdeadbeef`. Funds are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
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
