### Title
`execute_replace_class` Accepts Undeclared Class Hash Without Verifying Against `contract_class_changes` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler updates a contract's class hash in `contract_state_changes` without verifying that the supplied class hash is present in `contract_class_changes` (the declared-class registry). This is a direct analog to the original report's pattern: one state structure is mutated while a related, dependent structure is never consulted, producing a persistent inconsistency. Any contract can call `replace_class` with an arbitrary, undeclared hash, permanently rendering itself uncallable and freezing all funds it holds.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` processes the `replace_class` syscall. It reads the requested class hash from the syscall request, writes a new `StateEntry` with that hash into `contract_state_changes`, and appends a revert-log entry — but it never reads from or validates against `contract_class_changes`.

The function signature does not even carry `contract_class_changes` as an implicit argument, making the check structurally impossible at this call site:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
```

The developer acknowledged the missing check with an explicit TODO:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

After the gas check, the handler unconditionally writes the attacker-supplied hash:

```cairo
let class_hash = request.class_hash;
// ...
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
dict_update{dict_ptr=contract_state_changes}(
    key=contract_address,
    prev_value=cast(state_entry, felt),
    new_value=cast(new_state_entry, felt),
);
``` [1](#0-0) 

The state inconsistency is structural: `contract_state_changes` now records a class hash that has no corresponding entry in `contract_class_changes`. Every subsequent call to that contract address will attempt to dispatch to a class that does not exist in the class commitment tree, causing the call to fail unconditionally.

Compare with `execute_declare_transaction`, which correctly writes to `contract_class_changes` before a class hash becomes usable:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

`execute_replace_class` performs no equivalent lookup or assertion against `contract_class_changes`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract whose class hash is set to an undeclared value becomes permanently uncallable. Any ERC-20 balances, ETH, or other assets held in that contract's storage are irrecoverably frozen: no entry point can be dispatched, no withdrawal can succeed, and no upgrade path exists because the upgrade mechanism itself is the broken syscall. The state root committed on-chain will encode the invalid class hash, making the freeze permanent across all future blocks.

---

### Likelihood Explanation

The attack requires only the ability to deploy a contract and invoke a single syscall — both are available to any unprivileged user. A malicious actor can:

1. Deploy a contract that accepts user deposits (e.g., a fake vault or bridge).
2. After accumulating victim funds, call `replace_class` with a felt value that is not a declared class hash (e.g., `1`).
3. The OS processes the syscall without complaint, commits the invalid class hash to state, and the contract is permanently bricked.

No privileged role, leaked key, or external dependency is required. The entry path is a standard user-submitted invoke transaction containing a `replace_class` syscall.

---

### Recommendation

Add `contract_class_changes: DictAccess*` as an implicit argument to `execute_replace_class` and perform a `dict_read` on the requested class hash before accepting it. Assert that the returned value is non-zero (i.e., the class has been declared). This mirrors the invariant enforced by `execute_declare_transaction`, which uses `prev_value=0` to guarantee a class is registered exactly once before it can be referenced. [3](#0-2) 

---

### Proof of Concept

1. Attacker declares a legitimate class `C` and deploys contract `A` using class `C`. Contract `A` accepts token deposits from users.
2. After users deposit funds into `A`, the attacker submits an invoke transaction that calls `A.__execute__`, which internally issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).
3. The OS dispatches to `execute_replace_class`. Gas is deducted. The handler writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` for address `A`. No lookup against `contract_class_changes` occurs.
4. The block is proven and the new state root is committed on-chain. Contract `A` now has class hash `0xdeadbeef` in the global state tree, but `0xdeadbeef` has no entry in the class commitment tree.
5. Any subsequent call to contract `A` — including withdrawal attempts by depositors — fails because the OS cannot resolve class `0xdeadbeef` to executable bytecode.
6. All deposited funds are permanently frozen with no recovery path. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
