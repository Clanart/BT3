### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary `class_hash` value from the `ReplaceClassRequest` without verifying that the hash corresponds to a class that has been declared in the contract class tree. An unprivileged contract deployer can exploit this to permanently brick a contract — freezing any funds it holds — by replacing the contract's class with an undeclared hash. The OS itself acknowledges this gap with a TODO comment that has passed its planned resolution date.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 877–916) reads the requested `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any check that the hash is present in `contract_class_changes` (the declared-class tree):

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

The TODO comment — dated 2026-01-01, now six months past — explicitly acknowledges the missing guard. No validation is performed anywhere in the function before the state update is committed.

Compare this with `execute_declare_transaction`, which enforces `prev_value=0` to guarantee a class is declared at most once:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
```

`execute_replace_class` has no symmetric guard on the *new* value side.

The analog to the original report's vulnerability class is: just as `build(X)` could be called to pre-populate `proxies[X]` and cause `setOwner(X)` to revert on its `require`, here a contract can call `replace_class(undeclared_hash)` to write an invalid class hash into its own `StateEntry`. The OS proof for that block succeeds (no assertion fails), but the resulting committed state is permanently broken: the class hash stored on-chain has no corresponding class body, so the OS prover can never supply valid class data for any future call to that contract.

---

### Impact Explanation

Once a contract's `StateEntry.class_hash` is committed on-chain as an undeclared hash:

1. Any future transaction that calls the contract requires the prover to supply class data for that hash.
2. No valid class data exists (the hash was never declared), so the OS proof for any block containing such a call cannot be generated.
3. The sequencer is forced to exclude all calls to the contract from every future block.
4. The contract is permanently inaccessible — its entry points, including any withdrawal or recovery function, can never execute.
5. All funds held by the contract are permanently frozen.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The attack path requires only an unprivileged contract deployer:

- Deploy a contract that accepts deposits from other users (e.g., a vault or escrow).
- After users deposit funds, call `replace_class` with any felt value that has not been declared (e.g., `0x1`).
- The OS executes `execute_replace_class`, writes the undeclared hash into the contract's `StateEntry`, and includes the block in the proof without error.
- The contract is permanently bricked; deposited funds are permanently frozen.

No privileged role, no key leak, no network-level attack, and no third-party compromise is required. The TODO comment confirms the development team is aware of the missing check, and the planned fix date (2026-01-01) has already passed without the guard being added.

**Likelihood: High** — the syscall is publicly reachable by any contract, the missing check is acknowledged, and the attack requires a single transaction after deployment.

---

### Recommendation

Before committing the `dict_update` in `execute_replace_class`, verify that `request.class_hash` is present in `contract_class_changes` (i.e., was declared in the current block) or in the pre-existing class commitment tree. A minimal fix is to perform a `dict_read` on `contract_class_changes` for `request.class_hash` and assert the result is non-zero, mirroring the `prev_value=0` guard used in `execute_declare_transaction`. If the class was declared in a prior block (not the current one), the check must also consult the class commitment tree root provided to the OS.

---

### Proof of Concept

1. **Deploy vault**: Attacker deploys `Vault` contract (class `C`) that accepts `deposit()` and `withdraw()` calls.
2. **Users deposit**: Multiple users call `deposit()`, locking funds inside `Vault`.
3. **Attacker calls `replace_class(0x1)`**: From within `Vault`, the attacker triggers the `replace_class` syscall with `class_hash = 0x1` (not declared).
4. **OS executes `execute_replace_class`**: Lines 896–910 of `syscall_impls.cairo` write `StateEntry(class_hash=0x1, ...)` for `Vault`'s address into `contract_state_changes`. No assertion fails; the block proof is valid.
5. **State committed**: The blockchain now records `Vault.class_hash = 0x1`.
6. **All future calls fail**: Any block including a call to `Vault` requires the prover to supply class data for hash `0x1`. No such data exists; the OS proof cannot be generated. The sequencer must exclude all `Vault` calls.
7. **Funds permanently frozen**: `withdraw()` can never execute. User funds are irrecoverably locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
