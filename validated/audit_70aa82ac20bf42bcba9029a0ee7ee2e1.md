### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash from a calling contract and writes it directly into `contract_state_changes` without verifying that the hash corresponds to a class that has actually been declared on-chain. A developer-acknowledged TODO comment at the exact location of the missing check confirms this is an unimplemented guard. Any contract — including one holding user funds — can call `replace_class` with a non-existent class hash, rendering itself permanently uncallable and freezing all funds stored in it.

---

### Finding Description

In `execute_replace_class`, the OS reads `request.class_hash` directly from the syscall request and writes it into the contract's state entry with no cross-reference against `contract_class_changes` (the dictionary that tracks declared classes):

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

Compare this with `execute_declare_transaction`, which enforces `prev_value=0` and `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`, ensuring a class is only accepted once and is non-trivial: [2](#0-1) 

No equivalent guard exists in `execute_replace_class`. The `class_hash` field of the request is a raw felt supplied by the calling contract's code; the OS imposes no constraint that it must appear as a key in `contract_class_changes`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is overwritten with an undeclared value, every subsequent call to that contract will fail at class-lookup time: the sequencer cannot locate compiled bytecode for the unknown hash, so every invocation reverts. Because `replace_class` itself is only callable from within the contract's own execution, there is no recovery path — the contract cannot call `replace_class` again to restore a valid class. All ERC-20 balances, NFT ownership records, vault deposits, or any other assets stored in the contract's storage become permanently inaccessible.

---

### Likelihood Explanation

The `replace_class` syscall is reachable by any unprivileged transaction sender who either:

1. **Deploys a shared-custody contract** (multisig, vault, AMM pool) that contains a code path — intentional or accidental — that calls `replace_class` with an attacker-controlled or zero-initialized hash; or
2. **Exploits a reentrancy or input-validation bug** in an existing contract to redirect a `replace_class` call to an arbitrary hash.

No privileged operator role is required. The OS proof remains valid regardless of whether the new class hash is declared, so the sequencer will include the block and the state transition is finalized on L1. The TODO comment confirms the developers are aware the check is absent, meaning the window of exposure is open until the fix is shipped.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists as a key in `contract_class_changes` (i.e., that it was declared in the current or a prior block). Concretely, add a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled-class hash is non-zero, mirroring the `assert_not_zero(compiled_class_hash)` guard already present in `execute_declare_transaction`.

---

### Proof of Concept

1. Attacker deploys a vault contract `V` that accepts deposits from multiple users and exposes an `upgrade(new_class)` entry point with no access control.
2. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS executes `execute_replace_class`; `contract_state_changes[V].class_hash` is set to `0xdeadbeef`. No validation fires.
4. The block is proven and finalized on L1 — the state transition is canonical.
5. Any subsequent `invoke` targeting `V` causes the sequencer to look up compiled bytecode for `0xdeadbeef`, find nothing, and revert. The revert is permanent.
6. All user deposits inside `V`'s storage are frozen with no recovery mechanism. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
