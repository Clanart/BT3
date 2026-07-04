### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not validate that the new `class_hash` supplied via the `replace_class` syscall corresponds to a declared contract class. Any contract can set its own class hash to an arbitrary felt value — including `UNINITIALIZED_CLASS_HASH` (0) or any undeclared hash — permanently bricking itself and freezing any funds it holds. The OS itself acknowledges this gap with an explicit TODO comment at the exact location of the missing check.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 877–916) reads `class_hash` directly from the `ReplaceClassRequest` and writes it unconditionally into `contract_state_changes`:

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

There is **no assertion** that:
- `class_hash != UNINITIALIZED_CLASS_HASH` (0), and
- `class_hash` exists as a key in `contract_class_changes` (i.e., it was previously declared via a `declare` transaction).

The same defect exists in the deprecated path: [2](#0-1) 

The `UNINITIALIZED_CLASS_HASH` sentinel is defined as `0`: [3](#0-2) 

`get_contract_state_hash` treats `class_hash == 0` as a special case only when storage and nonce are also zero: [4](#0-3) 

Therefore, a contract that has non-zero storage (i.e., holds token balances or other state) and calls `replace_class(0)` will produce a state entry with `class_hash = 0` but non-zero storage root. The contract remains in the state tree with funds intact, but no executable class exists — funds are permanently frozen.

The vulnerability class is identical to the external report: **missing validation of a critical identity/type field** that the protocol assumes is constrained but never enforces at the OS level.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After a successful `replace_class(0)` or `replace_class(<undeclared_hash>)`:

1. The contract's `StateEntry.class_hash` is set to an invalid value.
2. All subsequent calls to the contract fail at entry-point dispatch — no compiled class exists for the hash.
3. Any ERC-20 balances, ETH/STRK, or other assets stored in the contract's storage are permanently inaccessible.
4. The OS commits this invalid state to the Patricia tree, making it irreversible on-chain.

A concrete griefing/rug scenario: a malicious deployer creates a contract that accepts deposits from users (e.g., a fake yield vault). After accumulating user funds, the deployer calls `replace_class(0)`. The OS accepts the transition without validation. All deposited funds are permanently frozen — the deployer cannot withdraw them either, but the users lose access permanently.

---

### Likelihood Explanation

- **Reachable by any contract deployer** — `replace_class` is a standard Sierra syscall available to every deployed contract; no privileged role is required.
- The OS-level TODO comment (`// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.`) confirms the check is **intentionally absent** in the current production code, not merely overlooked.
- Contracts with upgrade mechanisms that do not independently validate the new class hash (a common pattern) are silently exposed.
- The deprecated path (`deprecated_execute_syscalls.cairo`) carries the same defect, widening the attack surface to legacy Cairo 0 contracts.

---

### Recommendation

In `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add two guards before writing the new `StateEntry`:

1. **Non-zero check**: `assert_not_zero(class_hash)` — reject `UNINITIALIZED_CLASS_HASH`.
2. **Declared-class check**: read `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero, confirming the class was previously declared.

This mirrors the fix described in the external report: just as the Solana fix checked that invoke accounts cannot have data (ruling out program accounts), the StarkNet fix must check that `replace_class` targets cannot use undeclared hashes (ruling out phantom classes).

---

### Proof of Concept

```
1. Attacker deploys ContractA (a fake vault) with a legitimate-looking class.
2. Users deposit STRK/ETH into ContractA (storage now non-zero).
3. Attacker calls replace_class(class_hash=0) from ContractA.
4. OS executes execute_replace_class:
     - class_hash = 0 is read from request (no validation).
     - new StateEntry(class_hash=0, storage_ptr=..., nonce=...) is written.
     - dict_update commits class_hash=0 to contract_state_changes.
5. Block is proven; state root updated with ContractA.class_hash = 0.
6. Any call to ContractA → entry-point dispatch finds no compiled class → reverts.
7. User funds in ContractA.storage are permanently inaccessible.
   get_contract_state_hash(class_hash=0, storage_root≠0, nonce) → non-zero hash
   → contract remains in state tree with funds, but no code can execute.
``` [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-328)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L55-61)
```text
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
    }
```
