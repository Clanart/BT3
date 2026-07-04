### Title
Missing Validation of Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash supplied by a contract corresponds to a previously declared class. This is directly analogous to the reported vulnerability class — a critical identifier (class hash) is accepted and committed to state without validation. A contract can replace its own class with an arbitrary, undeclared class hash, rendering itself permanently non-executable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` from the syscall request and immediately uses it to update the contract's `StateEntry` in `contract_state_changes`, without checking whether that class hash has ever been declared on-chain:

```cairo
func execute_replace_class{...}(contract_address: felt) {
    ...
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
    ...
}
```

The developer-acknowledged TODO at line 898 explicitly confirms the missing check. The OS commits the new (potentially invalid) class hash to the state diff unconditionally. There is no cross-reference against `contract_class_changes` (the dictionary of declared classes) or any other declared-class registry. [1](#0-0) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract's class hash is replaced with a value that does not correspond to any declared class, the contract becomes permanently non-executable: every future call to it will fail at class resolution time because the OS cannot find the class bytecode. Any ERC-20 tokens, ETH, or other assets held in that contract's storage become permanently inaccessible. There is no recovery path — the OS has committed an invalid class hash to the global state root, and no subsequent transaction can upgrade or rescue the contract because execution of the contract itself is broken.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself — no privileged role is required. A contract deployer can:

1. Declare and deploy a contract that accepts user deposits (e.g., a token vault or wallet).
2. After accumulating funds, invoke `replace_class` with an arbitrary undeclared felt value (e.g., `0xdead`).
3. The OS accepts the state transition without validation.
4. All deposited funds are permanently frozen.

Additionally, a legitimate contract with a programming error could accidentally call `replace_class` with an invalid hash. The OS is the last line of defense and should reject such transitions, but currently does not.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, verify that it exists in `contract_class_changes` (the declared-class dictionary). Specifically, perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the returned compiled class hash is non-zero (i.e., the class has been declared). This mirrors the pattern already used in `execute_deploy` and `deploy_contract`, which look up the class hash in state before proceeding. [2](#0-1) 

---

### Proof of Concept

1. Attacker declares a legitimate contract class `C_valid` and deploys contract `A` using it. Contract `A` holds a `replace_class` call in one of its entry points.
2. Users deposit funds into contract `A`.
3. Attacker sends a transaction invoking the entry point of `A` that calls `replace_class(class_hash=0xdeadbeef)` — a value never declared on-chain.
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the syscall request.
   - Skips the missing declared-class check (the TODO at line 898).
   - Calls `dict_update` to set `A`'s `StateEntry.class_hash = 0xdeadbeef`.
5. The state root is updated to reflect `A` having class `0xdeadbeef`.
6. Any subsequent transaction targeting contract `A` fails: the OS cannot resolve class `0xdeadbeef` to any bytecode.
7. All funds in `A`'s storage are permanently frozen with no recovery path. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-66)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```
