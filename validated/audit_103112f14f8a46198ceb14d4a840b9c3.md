### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to an actually-declared contract class. An unprivileged contract deployer can exploit this to permanently set a contract's class hash to an undeclared value, rendering the contract permanently inoperable and freezing all funds held within it.

---

### Finding Description

The `execute_replace_class` function handles the `replace_class` syscall. It reads the requested new class hash from the syscall pointer and directly writes it into the contract state dictionary without any check that the hash is a known, declared class:

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

The TODO comment at line 898 is an explicit developer acknowledgment that this validation is absent. The OS accepts any felt value as the new class hash with no lookup against `contract_class_changes` to confirm the hash has a corresponding compiled class.

Once the state is committed with an undeclared class hash, every future call to that contract address will fail at class resolution time inside the OS execution loop, because no compiled class entry exists for that hash. The contract is permanently inoperable. There is no recovery path: the class hash is committed to the proven state, and no subsequent transaction can undo it.

---

### Impact Explanation

Any funds (native tokens, ERC-20 balances, or arbitrary storage-tracked assets) held by a contract whose class hash has been replaced with an undeclared value are permanently frozen. No `__execute__`, `__validate__`, or any other entry point can ever be reached again. This matches **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The attacker role is **contract deployer**, which is an explicitly listed unprivileged entry point in the StarkNet protocol. The attack path is:

1. Attacker deploys a contract whose code contains a callable function that issues `replace_class(undeclared_hash)`.
2. The contract is presented as a legitimate vault, escrow, or DeFi primitive; users deposit funds.
3. Attacker invokes the backdoor function (or it triggers automatically after a threshold condition).
4. The OS executes `execute_replace_class` with the undeclared hash, passes no validation, and commits the invalid class hash to state.
5. All subsequent calls to the contract revert at class resolution; funds are permanently frozen.

No privileged access, leaked keys, or network-level attack is required. The only prerequisite is the ability to deploy a contract — available to any StarkNet user.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, perform a lookup in `contract_class_changes` (or the global compiled class registry) to assert that `class_hash` maps to a non-zero compiled class hash. Reject the syscall with a failure response (analogous to `write_failure_response`) if no such entry exists. This mirrors the fix described in the reference report: adding an explicit guard that restricts the operation to valid, pre-existing state.

---

### Proof of Concept

```cairo
// Malicious contract code (pseudoCairo)
@external
func freeze_funds{syscall_ptr: felt*, ...}() {
    // 0xdead is not declared anywhere on-chain.
    replace_class(class_hash=0xdead);
    return ();
}
```

Execution trace through the OS:

1. Attacker submits an `invoke` transaction calling `freeze_funds`.
2. OS dispatches `REPLACE_CLASS_SELECTOR` → `execute_replace_class` in `syscall_impls.cairo` line 878.
3. `class_hash = 0xdead` is read from the syscall request (line 896).
4. **No validation is performed** (line 898 TODO is unimplemented).
5. `dict_update` at line 906 writes `StateEntry(class_hash=0xdead, ...)` into `contract_state_changes`.
6. Block is proven and state is committed.
7. Any future call to the contract address attempts to resolve class hash `0xdead` → no compiled class found → permanent revert.
8. All funds locked in the contract are irrecoverably frozen. [2](#0-1)

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
