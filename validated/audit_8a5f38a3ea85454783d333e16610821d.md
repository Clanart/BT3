### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash as the replacement target without verifying that the hash corresponds to a declared class. This creates an inconsistency analogous to the reference report: one part of the OS permits an operation (replacing a class with any hash) while another part of the OS requires a declared class to execute entry points. The result is that a contract can be permanently bricked — and any funds it holds permanently frozen — by replacing its class hash with an undeclared value.

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash from the syscall request and immediately writes it into `contract_state_changes` without any check that the hash corresponds to a declared class:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The OS commits this state transition unconditionally. Once committed, the contract's on-chain class hash points to an undeclared class. Any subsequent call to that contract will fail at the class-lookup stage because no compiled class exists for that hash. There is no recovery path: the contract cannot upgrade itself (its entry points are unreachable), and no external mechanism exists to reset the class hash.

The inconsistency mirrors the reference report's pattern:
- **Part A** (`execute_replace_class`): permits any felt as the new class hash — no existence gate.
- **Part B** (entry point execution): requires the class hash to resolve to a declared, compiled class before any entry point can run.

These two parts are in conflict. The OS enforces the second invariant at execution time but not at the state-write time of `replace_class`, leaving a window where an invalid state is committed.

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the transaction is finalized:
- All entry points of the contract become permanently unreachable.
- Any ERC-20 balances, ETH, or other assets held by the contract are irrecoverably locked.
- The contract cannot self-upgrade because `replace_class` itself is an entry point that requires a valid class to execute.

### Likelihood Explanation

**High.** The `replace_class` syscall is callable by any contract during normal execution. A malicious contract deployer can:
1. Deploy a contract that accepts user deposits.
2. After accumulating funds, call `replace_class` with a random undeclared felt (e.g., `1`).
3. The OS commits the invalid class hash; the contract is permanently bricked; deposited funds are frozen.

No privileged role, leaked key, or external dependency is required. The attacker only needs to deploy a contract and submit a transaction — both are unprivileged operations available to any user.

### Recommendation

Before committing the state update in `execute_replace_class`, verify that `request.class_hash` exists in `contract_class_changes` (or the global declared-class set). Specifically, perform a lookup analogous to the check done before executing an entry point: confirm that the hash maps to a non-zero compiled class hash. If the class is not declared, write a failure response and do not update `contract_state_changes`.

### Proof of Concept

1. Attacker deploys `VaultContract` with a legitimate class hash `C`. Users deposit funds.
2. Attacker calls a function on `VaultContract` that internally invokes the `replace_class` syscall with `new_class_hash = 0xdeadbeef` (an undeclared felt).
3. `execute_replace_class` runs:
   - Reads `request.class_hash = 0xdeadbeef`.
   - Skips the missing existence check (line 898 TODO).
   - Calls `dict_update` setting `VaultContract`'s class hash to `0xdeadbeef`.
   - Appends a `CHANGE_CLASS_ENTRY` revert log entry.
4. The transaction succeeds and is finalized. The OS state now records `VaultContract.class_hash = 0xdeadbeef`.
5. Any subsequent call to `VaultContract` (withdraw, transfer, etc.) fails: the OS cannot find a compiled class for `0xdeadbeef`. All deposited funds are permanently frozen. [2](#0-1)

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
