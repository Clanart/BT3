### Title
Missing Zero Class Hash Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the supplied `class_hash` is non-zero before writing it into the contract state. Because `0` is the protocol-defined `UNINITIALIZED_CLASS_HASH` sentinel, any contract can call `replace_class(0)` to permanently brick itself, freezing all funds held in its storage.

---

### Finding Description

**Vulnerability class**: Invalid transaction/state acceptance — missing null-value guard on a critical state-mutation syscall.

In `syscall_impls.cairo` the new-syscall handler for `replace_class` reads the caller-supplied `class_hash` and immediately writes it to the contract state with no zero-check:

```cairo
// syscall_impls.cairo ~line 896-910
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

The same omission exists in the deprecated-syscall path:

```cairo
// deprecated_execute_syscalls.cairo ~line 311-328
let class_hash = syscall_ptr.class_hash;
...
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
``` [2](#0-1) 

The protocol constant `UNINITIALIZED_CLASS_HASH = 0` is defined in `commitment.cairo` as the sentinel meaning "no contract deployed here": [3](#0-2) 

`get_contract_state_hash` in the same file treats `class_hash == 0` as the empty/uninitialized leaf: [4](#0-3) 

The developers themselves flag the missing validation with a TODO comment at the exact location of the bug: [5](#0-4) 

After `replace_class(0)` succeeds:

1. The contract's `StateEntry.class_hash` is written as `0`.
2. Every subsequent call to the contract resolves `class_hash = 0` and attempts to look up a compiled class for hash `0`. No such class exists; the entry-point lookup fails and the call reverts.
3. The contract's storage (and any token balances it holds) remains in the Merkle tree but is permanently inaccessible — no function can ever execute successfully again.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A malicious actor deploys a contract that accepts user deposits (e.g., a fake yield vault, escrow, or bridge adapter). After accumulating user funds, the attacker issues a single `replace_class(0)` syscall from within the contract. The OS accepts the call without error, writes `class_hash = 0` to the state, and from that point forward every call to the contract reverts. All deposited assets are permanently frozen with no recovery path at the protocol level.

---

### Likelihood Explanation

**Medium.** The attack requires:
- Deploying a contract (permissionless — any account can do this).
- Convincing users to deposit funds (social engineering, but realistic given DeFi norms).
- Issuing one syscall (`replace_class(0)`) — no privileged key or operator role needed.

The missing check is in the OS-level syscall handler, so no contract-level guard can prevent it once the syscall is issued. The TODO comment confirms the developers are aware the check is absent.

---

### Recommendation

1. Add `assert_not_zero(class_hash)` immediately after reading `class_hash` from the request in both `execute_replace_class` implementations (`syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`).
2. Implement the acknowledged TODO: verify that `class_hash` corresponds to a previously declared class (i.e., it exists as a key in `contract_class_changes` with a non-zero compiled class hash) before accepting the replacement.

---

### Proof of Concept

```
1. Attacker deploys MaliciousVault contract (Cairo 1 or Cairo 0).
2. MaliciousVault exposes a public `deposit()` entry point; users send tokens.
3. Attacker calls any entry point that internally executes:
       replace_class(class_hash=0)   // syscall with zero hash
4. OS execute_replace_class() runs:
       class_hash = 0                // no assert_not_zero here
       new_state_entry.class_hash = 0
       dict_update(contract_state_changes, ...)
5. Block is proven; state root updated with MaliciousVault.class_hash = 0.
6. Any future call_contract(MaliciousVault, ...) resolves class_hash=0,
   finds no compiled class, reverts.
7. All user funds in MaliciousVault storage are permanently frozen.
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L55-59)
```text
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
```
