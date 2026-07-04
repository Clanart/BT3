### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value from the caller without verifying that the hash corresponds to a previously declared class. This is an exact analog of the external report's "missing input validation in constructor" class: a critical parameter (`class_hash`) is accepted without a min/existence check. A contract deployer can exploit this to permanently freeze all funds held by a contract by replacing its class with an undeclared hash, after which no entry point can ever be executed on that contract.

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any check that the hash corresponds to a declared class:

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

The TODO comment at line 898 explicitly acknowledges this missing check. The OS accepts the state transition unconditionally.

Contrast this with `execute_declare_transaction`, which does enforce `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`, and with `deploy_contract`, which enforces that the target address is uninitialized. No equivalent guard exists for `replace_class`.

When any subsequent call is made to a contract whose class hash has been set to an undeclared value, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared hash → returns 0
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // key = 0, not present → panic
);
```

`find_element` from the Cairo standard library panics (hard abort) when the key is absent. There is no graceful error path for this case in `execute_entry_point`. The contract becomes permanently non-executable.

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds user funds (e.g., a token vault, escrow, or multi-sig) can have its class replaced with an undeclared hash. Once committed to state, no entry point — including withdrawal functions — can ever be executed on that contract again. All funds locked inside are permanently frozen with no recovery path, because the OS will never be able to look up a compiled class for the invalid hash.

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context. No privileged role is required. A malicious contract deployer can:
1. Deploy a contract that accepts user deposits.
2. After accumulating funds, invoke `replace_class` with a felt value that has never been declared (e.g., `0xdeadbeef`).
3. The OS accepts the state transition without validation.
4. All deposited funds are permanently frozen.

The attacker is an unprivileged contract deployer — exactly the entry path described in the bounty scope. The missing check is confirmed by the in-code TODO comment, meaning the developers are aware the validation is absent.

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it has been declared). The check should mirror the pattern used in `execute_declare_transaction`:

```cairo
// Verify the class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This ensures that `replace_class` can only transition a contract to a class that has a valid compiled class backing it, preventing the permanent-freeze scenario.

### Proof of Concept

1. Deploy contract `Vault` that accepts ETH deposits via `deposit()` and exposes `withdraw()`.
2. Users deposit funds into `Vault`.
3. Attacker (owner of `Vault`) sends a transaction that calls `replace_class(class_hash=0xdeadbeef)` from within `Vault`.
4. `execute_replace_class` in the OS writes `class_hash=0xdeadbeef` into `contract_state_changes` for `Vault`'s address — no validation occurs (line 898 TODO).
5. State is committed. `Vault`'s `StateEntry.class_hash` is now `0xdeadbeef`.
6. Any user attempts `withdraw()` on `Vault`. The OS calls `execute_entry_point`, performs `dict_read(key=0xdeadbeef)` → returns `0`, then calls `find_element(key=0)` → **panic / OS proof failure**.
7. All funds in `Vault` are permanently frozen; no proof can be generated for any block that includes a call to `Vault`. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
