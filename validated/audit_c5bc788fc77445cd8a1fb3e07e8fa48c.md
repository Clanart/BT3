### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program does not validate that the caller-supplied class hash corresponds to a declared contract class. An unprivileged contract deployer can exploit this to permanently freeze all funds held by any contract they control, by replacing the contract's class with an arbitrary, undeclared class hash. The same gap exists in the deprecated syscall path.

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in the set of declared classes:

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

The TODO comment is an explicit acknowledgment by the developers that this check is missing. The identical gap exists in the deprecated path in `deprecated_execute_syscalls.cairo`.

The OS program accepts any felt value as the new class hash, commits it to the state diff, and the resulting state root is finalized on L1. Once the state root is committed, the contract permanently carries an undeclared class hash. Any future attempt to execute the contract will fail at the proving stage because the compiled class for that hash does not exist in `compiled_class_facts`, making the contract's storage and funds permanently inaccessible.

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract whose class hash is replaced with an undeclared value can never be executed again. All tokens, ETH, or other assets held in that contract's storage become permanently frozen. Because the state root is committed to L1 via the OS output, there is no on-chain recovery path.

### Likelihood Explanation

Any contract deployer can write a contract that calls `replace_class` with an arbitrary felt. The attacker's cost is only the gas for the syscall. A realistic attack scenario:

1. Attacker deploys a DeFi contract (e.g., a vault or liquidity pool) that appears legitimate.
2. Users deposit funds into the contract.
3. Attacker sends a transaction that causes the contract to call `replace_class` with an arbitrary, undeclared class hash (e.g., `0x1`).
4. The OS executes the syscall, updates `contract_state_changes` with the invalid class hash, and the state root is committed to L1.
5. All funds in the contract are permanently frozen — no withdrawal, no upgrade, no recovery.

The entry point is fully unprivileged: any contract deployer can trigger this path.

### Recommendation

Before writing the new class hash into `contract_state_changes`, the OS must verify that the hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero. This is exactly what the existing TODO comment calls for.

### Proof of Concept

**Vulnerable code — `syscall_impls.cairo`:**

```cairo
let class_hash = request.class_hash;
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
// ← NO VALIDATION HERE
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, ...
);
dict_update{dict_ptr=contract_state_changes}(...);
```

**Attack contract (pseudocode):**
```cairo
@external
func freeze_self() {
    // 0xdeadbeef is not a declared class hash
    replace_class(class_hash=0xdeadbeef);
}
```

**Steps:**
1. Deploy the attack contract holding user funds.
2. Call `freeze_self()`.
3. The OS executes `execute_replace_class`, writes `class_hash=0xdeadbeef` into `contract_state_changes` with no validation.
4. State root is updated and committed to L1.
5. All funds in the contract are permanently frozen.

**Root cause references:** [1](#0-0) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
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
}
```
