### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds via `library_call` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS does not validate that the supplied class hash corresponds to a declared contract class. Combined with the `library_call` syscall (the StarkNet analog of EVM `DELEGATECALL`), a malicious class can replace the calling contract's class hash with an undeclared value, permanently freezing the contract and any funds it holds. This is a direct analog to the MetaSwap adapter-registry overwrite bug: just as a DELEGATECALL'd adapter could overwrite the adapter mapping, a `library_call`'d class can overwrite the calling contract's class registration.

---

### Finding Description

**Root cause — missing validation in `execute_replace_class`:** [1](#0-0) 

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

The `class_hash` field from the syscall request is written directly into `contract_state_changes` with **no check** that it exists in the declared class registry (`contract_class_changes`). The TODO comment at line 898 explicitly acknowledges this omission.

**The `library_call` propagation path (DELEGATECALL analog):**

`execute_library_call` executes an arbitrary class's code while preserving the **caller's** `contract_address` in the execution context: [2](#0-1) 

```cairo
tempvar execution_context: ExecutionContext* = new ExecutionContext(
    entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
    class_hash=request.class_hash,          // ← library class code runs here
    ...
    execution_info=new ExecutionInfo(
        ...
        contract_address=caller_execution_info.contract_address,  // ← CALLER's address
        ...
    ),
```

When `execute_syscalls` dispatches `REPLACE_CLASS_SELECTOR`, it passes `execution_context.execution_info.contract_address` — which, inside a library call, is the **caller's** address: [3](#0-2) 

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
```

So a library call's code that invokes `replace_class` modifies the **calling contract's** class hash — exactly the MetaSwap pattern where a DELEGATECALL'd adapter overwrites the registry of the calling contract.

**Why this causes permanent freezing:**

When the victim contract is subsequently called, `execute_entry_point` reads the (now-invalid) class hash and looks it up in `contract_class_changes`: [4](#0-3) 

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

`dict_read` on an undeclared key returns the default value `0` (from `dict_new()`). `find_element` with key `0` then searches the compiled class bundle for a class with hash `0`. Since no such class exists, the prover **cannot generate a valid proof** for any future call to the victim contract. The contract becomes permanently uncallable, and all funds it holds are permanently frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 tokens, ETH, or other assets held in a contract whose class hash is replaced with an undeclared value are permanently inaccessible. No valid proof can be generated for calls to the contract, so no withdrawal, transfer, or recovery function can ever execute. The state is irreversible because the class hash is committed to the global state root.

---

### Likelihood Explanation

**Medium.** The attack requires one of two realistic conditions:

1. **Malicious library class:** A contract calls `library_call` with a class hash supplied or influenced by an attacker (e.g., an upgradeable proxy pattern where the implementation address is user-configurable). The malicious class calls `replace_class(undeclared_hash)`, freezing the proxy.

2. **Unguarded `replace_class` call:** A contract exposes a publicly callable upgrade function that passes user-supplied data to `replace_class` without access control. An unprivileged user calls it with an undeclared hash.

Both scenarios are realistic in DeFi protocols with upgrade mechanisms. The attacker entry point is an ordinary `invoke` transaction — no privileged role is required.

---

### Recommendation

Implement the missing validation noted in the TODO at line 898 of `syscall_impls.cairo`. Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes` with a non-zero compiled class hash:

```cairo
// Validate that the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the MetaSwap fix: disallow modification of the registry to point to an unregistered entry.

---

### Proof of Concept

1. **Declare** a malicious class `M` whose sole logic is:
   ```
   replace_class_syscall(0xdeadbeef_undeclared_hash)
   ```
   `M` is a valid declared class (it has a compiled class hash in the registry).

2. **Deploy** a victim contract `V` that holds user funds and exposes:
   ```
   fn set_implementation(impl_hash: ClassHash) {
       library_call_syscall(impl_hash, some_selector, [])
   }
   ```

3. **Attacker** (unprivileged) sends an `invoke` transaction calling `V.set_implementation(M)`.

4. The OS executes `execute_library_call` with `class_hash=M` and `contract_address=V`.

5. Inside the library call, `execute_replace_class` is called with `contract_address=V` and `class_hash=0xdeadbeef`. No validation occurs (the TODO check is absent).

6. `contract_state_changes[V].class_hash` is updated to `0xdeadbeef`.

7. All subsequent calls to `V` reach `execute_entry_point`, which reads `contract_class_changes[0xdeadbeef] = 0` (undeclared), then calls `find_element(..., key=0)`, which cannot be satisfied by any valid prover witness.

8. `V` is permanently uncallable. All funds in `V` are permanently frozen. [5](#0-4) [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L237-278)
```text
func execute_library_call{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    let request = cast(syscall_ptr + RequestHeader.SIZE, LibraryCallRequest*);
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=LIBRARY_CALL_GAS_COST, request_struct_size=LibraryCallRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    // Prepare execution context.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=request.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_execution_info.caller_address,
            contract_address=caller_execution_info.contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );

    return contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );
}
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
