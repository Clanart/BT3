### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a declared class in the class tree. This mirrors the external report's pattern exactly: a legitimate state-mutating operation leaves a contract in an inconsistent state (class hash pointing to a non-existent class), the contract object is not destroyed, and all subsequent calls to it permanently fail — freezing any funds held within.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` processes the `replace_class` syscall. It reads `request.class_hash` and immediately writes it into the contract's `StateEntry` with no check that the hash exists in `contract_class_changes` (the declared class tree):

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

The TODO comment is an explicit developer acknowledgment that this validation is absent.

**Structural analogy to the external report:**

| External Report (veKITTEN) | StarkNet OS Analog |
|---|---|
| `withdraw()`/`merge()`/`split()` zeroes `locked[_tokenId].amount` and `.end` | `replace_class` sets `class_hash` to an undeclared hash |
| Token is NOT burned; it still exists | Contract is NOT destroyed; it still exists in `contract_state_changes` |
| `deposit_for()` reverts because `_locked.amount == 0` | All future calls revert because the class cannot be loaded |
| Rewards permanently frozen | Funds permanently frozen |

When a contract's `class_hash` is set to a value absent from the class tree, every subsequent call to that contract will fail at class-loading time. Because `replace_class` is the only mechanism to change a class hash, and the contract cannot execute any entry point to call it again, the state is irrecoverable. [1](#0-0) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to a non-existent value:

1. Every `call_contract` or `library_call` targeting that address reads the invalid `class_hash` from `contract_state_changes` and attempts to load the class. The class does not exist; execution fails.
2. The contract cannot self-recover: it cannot execute `replace_class` again because no entry point can run.
3. All ERC-20 balances, ETH, or other assets stored in the contract's storage subtree are permanently inaccessible — there is no protocol-level escape hatch. [2](#0-1) 

---

### Likelihood Explanation

**Low.**

The triggering condition requires a contract to emit a `replace_class` syscall with a class hash that was never declared. This can occur via:

- A bug in an upgrade-management contract that passes the wrong hash (e.g., off-by-one in a hash array, uninitialized variable).
- A malicious contract deliberately designed to self-destruct after attracting deposits.

Because `replace_class` is a standard, unprivileged syscall available to any Cairo contract, no special role or key is required. The OS is the necessary vulnerable step: it is the only component that could enforce the existence check but does not.

---

### Recommendation

Before writing the new `StateEntry`, validate that `class_hash` is present in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero (`!= UNINITIALIZED_CLASS_HASH`). This is exactly what the existing TODO comment calls for. [3](#0-2) 

---

### Proof of Concept

1. **Attacker deploys** `VaultContract` — a contract that accepts token deposits and stores balances in its storage.
2. Users deposit funds; `VaultContract` accumulates a non-zero storage tree.
3. **Attacker (owner of `VaultContract`) calls `replace_class`** with `class_hash = 0xdeadbeef` (an arbitrary felt never declared via `declare`).
4. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No existence check is performed (the TODO is unimplemented).
   - `dict_update` writes `StateEntry { class_hash: 0xdeadbeef, ... }` into `contract_state_changes`.
5. The block is proven and the new state root is committed. `VaultContract`'s leaf in the contract state tree now encodes `class_hash = 0xdeadbeef`.
6. Any subsequent `call_contract` to `VaultContract` reads `class_hash = 0xdeadbeef`, attempts to load the class, finds nothing, and reverts.
7. All deposited funds are permanently frozen — no withdrawal function can ever execute. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```
