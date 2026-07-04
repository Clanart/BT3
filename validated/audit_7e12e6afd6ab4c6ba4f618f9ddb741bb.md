### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a declared contract class. A malicious contract can replace its own class hash with an undeclared or invalid hash, permanently rendering the contract unexecutable and freezing any funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function updates a contract's class hash in `contract_state_changes` without performing any check that the supplied `class_hash` has been previously declared on-chain. The missing check is explicitly acknowledged by a TODO comment in the production code:

```cairo
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

The OS has access to `contract_class_changes` (the dictionary of declared class hashes) but does not consult it here. Any felt value — including `0`, a random number, or a hash of a non-existent class — is accepted and committed to state.

The analogous root cause in the external report is `updateWeights` being callable without enforcing a delay or invariant check, allowing the caller to set state to an arbitrary value that breaks downstream accounting. Here, `replace_class` is callable without enforcing that the new class hash is declared, allowing a caller to set contract state to an invalid value that breaks future execution.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every subsequent transaction targeting that contract will fail at class resolution time: the OS cannot find a compiled class for the hash, so execution cannot proceed. Any ERC-20 tokens, ETH, or other assets stored in the contract's storage become permanently inaccessible. The state transition is valid from the OS's perspective (the `replace_class` call itself succeeds and is committed), but the resulting state is irrecoverable without a protocol-level intervention.

---

### Likelihood Explanation

The attack path requires only standard unprivileged actions:

1. Deploy a contract (any user can do this).
2. Attract user deposits into the contract (e.g., present it as a vault or bridge).
3. Issue a `replace_class` syscall from within the contract, supplying an arbitrary undeclared hash.
4. The OS accepts the call, commits the invalid class hash to state.
5. All subsequent calls to the contract revert; funds are frozen permanently.

No privileged role, leaked key, or external dependency is required. The syscall is available to any deployed contract.

---

### Recommendation

Before updating `contract_state_changes`, verify that the new `class_hash` exists in `contract_class_changes` (i.e., it has been declared in the current block or in a prior committed state). The check should assert that `dict_read` on `contract_class_changes` for the given `class_hash` returns a non-zero compiled class hash. This is exactly what the existing TODO comment describes and what the analogous `dict_update` with `prev_value=0` pattern enforces for `execute_declare_transaction`. [2](#0-1) 

---

### Proof of Concept

1. Attacker deploys contract `C` holding user funds (e.g., a token vault).
2. From within `C`, the attacker triggers a call that invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared hash).
3. The OS executes `execute_replace_class`:
   - Gas is deducted (`REPLACE_CLASS_GAS_COST`).
   - `state_entry` for `C` is fetched.
   - `new StateEntry(class_hash=0xdeadbeef, ...)` is written to `contract_state_changes` with no class existence check.
4. The block is proven and committed. The on-chain state for `C` now has `class_hash = 0xdeadbeef`.
5. Any subsequent `invoke` transaction targeting `C` causes the OS to look up `0xdeadbeef` in the compiled class facts — it is absent — and the transaction fails irrecoverably.
6. All funds in `C`'s storage are permanently frozen. [1](#0-0) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
