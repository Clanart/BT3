### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash corresponds to a declared contract class before updating the contract's state. This is a direct state-transition bypass: the OS allows a contract to transition to a class state that has never been declared, permanently rendering the contract non-functional and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the `class_hash` from the syscall request is applied directly to the contract's `StateEntry` in `contract_state_changes` with no check that the hash exists in the declared class set. The code itself contains an explicit acknowledgment of this missing guard:

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

The only validation performed is a gas check. The `class_hash` value is taken verbatim from the caller-controlled syscall request and written into the contract's state. There is no lookup against `contract_class_changes` (current-block declarations) or the global state tree (prior-block declarations).

The dispatch path in `execute_syscalls.cairo` routes directly to this function with no pre-validation:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
``` [2](#0-1) 

The analog to M-02 is exact: just as `applyCover` could be called without checking that the market was in `Trading` state, `replace_class` can be called without checking that the target class is in a `Declared` state. Both are state-machine bypasses where a transition is permitted regardless of whether the prerequisite state condition holds.

---

### Impact Explanation

When a contract's class hash is set to an undeclared value:

1. All subsequent calls to that contract require the OS to resolve the class from the compiled class facts bundle. Since the hash does not exist there, the execution cannot proceed — the proof for any block that attempts to call the contract would be invalid.
2. The sequencer is therefore forced to exclude all future transactions targeting that contract.
3. Any funds (STRK, ETH, or arbitrary tokens) held in the contract's storage are permanently inaccessible — the contract cannot execute any entry point, including any recovery or withdrawal function.
4. The contract cannot self-repair: since no entry point can execute, a second `replace_class` call to restore a valid hash is impossible.

This matches **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The attack is fully reachable by an unprivileged transaction sender with no special access:

1. An attacker deploys a contract that accepts user deposits and exposes a `freeze()` function that internally calls `replace_class(arbitrary_undeclared_hash)`.
2. Users deposit funds (tokens, ETH, STRK) into the contract.
3. The attacker submits a transaction calling `freeze()`. This transaction is valid from the sequencer's perspective: it has a correct signature, pays fees, and increments the nonce correctly.
4. The OS processes the `replace_class` syscall, writing the undeclared hash into `contract_state_changes` without any validation.
5. The state update is committed. The contract's class hash is now permanently set to an undeclared value.
6. All user funds are frozen.

No privileged role, leaked key, or malicious sequencer is required. The sequencer processes the transaction normally because it is structurally valid.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, the OS must verify that `class_hash` is present in the declared class set. Concretely, the check should confirm that `class_hash` maps to a non-zero compiled class hash — either in `contract_class_changes` (for classes declared in the current block) or in the global contract class Patricia tree (for classes declared in prior blocks). This is already identified as a TODO in the code at line 898 and must be promoted to an enforced Cairo assertion.

---

### Proof of Concept

**Step 1.** Attacker deploys `MaliciousVault`:
- `deposit()`: accepts STRK from users, stores balances.
- `freeze()`: calls `replace_class(0xdeadbeef_undeclared_hash)`.

**Step 2.** Legitimate users call `deposit()`, transferring funds into `MaliciousVault`.

**Step 3.** Attacker submits a transaction calling `freeze()`. The sequencer includes it (valid fee, valid nonce, valid signature).

**Step 4.** The OS executes `freeze()`. Inside, the `REPLACE_CLASS` syscall is dispatched to `execute_replace_class`. The function reads `class_hash = 0xdeadbeef_undeclared_hash` from the request and writes it directly into `contract_state_changes` for `MaliciousVault`'s address. [3](#0-2) 

**Step 5.** The block is proven and finalized. `MaliciousVault`'s on-chain class hash is now `0xdeadbeef_undeclared_hash`.

**Step 6.** Any future transaction targeting `MaliciousVault` causes the OS to attempt to resolve `0xdeadbeef_undeclared_hash` from the compiled class facts bundle — which fails. The block proof would be invalid, so the sequencer cannot include such transactions. All user funds are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-197)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
```
