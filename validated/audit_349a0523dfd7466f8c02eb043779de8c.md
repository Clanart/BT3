### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds - (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash supplied by a contract corresponds to a previously declared contract class. This is an exact structural analog to the `FathomProxyWalletOwner` bug: just as that contract allowed initialization with unsynchronized, unverified addresses, the OS here allows a contract to replace its class with an arbitrary, undeclared hash — permanently severing the link between the contract's on-chain state and any executable code, and freezing all funds held within it.

---

### Finding Description

In `execute_replace_class` in `syscall_impls.cairo`, the syscall reads the requested class hash directly from the syscall pointer and writes it into `contract_state_changes` with no check that the hash corresponds to a declared class:

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

The TODO comment on line 898 explicitly acknowledges the missing check. The OS accepts any felt value as the new class hash without consulting `contract_class_changes` (the declared class registry).

The downstream consequence is in `execute_entry_point.cairo`. When any future call targets the affected contract, the OS performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

If `class_hash` is undeclared, `dict_read` returns 0 (the default), and `find_element` panics because no compiled class with hash 0 exists. The contract becomes permanently unexecutable at the OS level. The sequencer must exclude all future calls to it, but the state — including any token balances stored in the contract — is irrecoverably locked.

---

### Impact Explanation

**Critical. Permanent freezing of funds.**

A contract that holds ERC-20 token balances, ETH, or STRK (e.g., a vault, AMM pool, or escrow) can have all its funds permanently frozen. Once the class hash is replaced with an undeclared value, no `call_contract`, `__execute__`, or any other entry point can ever be dispatched to it again. There is no recovery path: the state is committed on-chain, and no future transaction can restore the class hash to a valid value without the contract itself being callable (a circular dependency).

---

### Likelihood Explanation

The attack is reachable by any **contract deployer** — an explicitly allowed unprivileged role. The attacker:

1. Deploys a contract that appears legitimate (e.g., a yield vault or token bridge).
2. Users deposit funds.
3. The attacker calls a function in the contract that internally issues `replace_class(arbitrary_undeclared_felt)`.
4. The OS processes the syscall without error (no validation).
5. The contract's class hash in the committed state is now an undeclared value.
6. All funds are permanently frozen.

No privileged access, leaked keys, or operator collusion is required. The attack requires only the ability to deploy a contract and trigger a transaction — standard unprivileged capabilities.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `class_hash` as the key and assert the result is non-zero (a declared compiled class hash). This is exactly what the existing TODO acknowledges must be done.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract with a `drain_class()` function that calls `replace_class(0xdeadbeef_arbitrary_undeclared_hash)`.
2. Users deposit STRK tokens into `MaliciousVault` (balances stored in its storage).
3. Attacker calls `drain_class()`.
4. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef...` from the syscall request.
   - Skips the missing declared-class check (line 898 TODO).
   - Writes `StateEntry(class_hash=0xdeadbeef..., ...)` into `contract_state_changes`.
   - Transaction succeeds; OS proof is valid.
5. In all subsequent blocks, any call to `MaliciousVault` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef...)` → returns 0.
   - `find_element(..., key=0)` → panics (no compiled class with hash 0).
   - Sequencer must exclude all such calls.
6. All STRK balances inside `MaliciousVault` are permanently inaccessible.

**Root cause file/line:** [1](#0-0) 

**Downstream failure site:** [2](#0-1)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
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
```
