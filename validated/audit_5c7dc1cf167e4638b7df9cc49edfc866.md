### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a declared contract class. This is structurally analogous to the `selfdestruct` vulnerability in the external report: instead of erasing contract code, it replaces the contract's code pointer with an invalid/undeclared hash, permanently rendering the contract non-executable and freezing all funds held within it. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `REPLACE_CLASS` syscall. After deducting gas, it reads the caller's current `StateEntry` and writes a new `StateEntry` with the attacker-supplied `class_hash` directly into `contract_state_changes`, with no validation that the new class hash corresponds to any declared class:

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
```

The OS then proves this state transition as valid. Once the proof is accepted on L1, the contract's class hash in the canonical global state permanently points to non-existent code. Any subsequent call to the contract will fail at class resolution, and the contract's storage — including all token balances and locked funds — becomes permanently inaccessible.

The `selfdestruct` analog is exact: `selfdestruct` erases the code at an address; `replace_class(undeclared_hash)` replaces the code pointer with one that resolves to nothing, achieving the same irreversible effect on fund accessibility.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract holding user funds (e.g., a DeFi vault, a token bridge escrow, a multisig wallet) that calls `replace_class` with an undeclared hash — whether by a bug in the contract or by a malicious actor who controls the contract — will have its class hash permanently replaced in the proven state. The contract's storage (balances, locked assets) remains in the global state tree but is forever unreachable because no valid entry point can be dispatched. There is no recovery path: the OS has proven the transition valid, L1 has accepted the proof, and the state is final.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract on itself. A malicious contract deployer can deploy a contract that calls `replace_class(0)` or `replace_class(arbitrary_felt)` in any externally callable function. A user who interacts with such a contract (e.g., depositing funds first, then triggering the replace) would have their funds permanently frozen. Additionally, a legitimate contract with a bug in its upgrade logic could accidentally supply an undeclared hash. The missing check is explicitly flagged as a known gap in the OS code.

---

### Recommendation

Implement the missing validation noted in the TODO comment. Before writing the new `StateEntry`, the OS must verify that `class_hash` exists in `contract_class_changes` (i.e., it was declared in the current block) or is present in the existing class commitment tree. This mirrors how `execute_deploy` validates the class hash against declared classes before deploying. The check should be enforced at the OS level so that no proof can be generated for a `replace_class` call referencing an undeclared hash, regardless of sequencer behavior.

---

### Proof of Concept

1. Attacker deploys contract `C` with an `upgrade()` function that calls `replace_class(0x0)` (or any undeclared felt).
2. Victim deposits funds into contract `C` (e.g., calls a `deposit()` function that writes a balance to storage).
3. Attacker calls `upgrade()` on contract `C`. The `REPLACE_CLASS` syscall is processed by `execute_replace_class`.
4. The OS reads `request.class_hash = 0x0`, skips the missing validation (line 898 TODO), and writes `new StateEntry(class_hash=0x0, ...)` into `contract_state_changes`.
5. The block is proven. The L1 verifier accepts the proof. The global state now records contract `C` with `class_hash = 0x0`.
6. Any future call to contract `C` fails: the OS cannot resolve class `0x0` to any compiled class. The victim's deposited funds are permanently frozen with no withdrawal path.

---

**Relevant code locations:** [1](#0-0) 

The explicit TODO acknowledging the missing check: [2](#0-1) 

The syscall dispatch point in `execute_syscalls.cairo` confirming this is reachable from any contract execution: [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
