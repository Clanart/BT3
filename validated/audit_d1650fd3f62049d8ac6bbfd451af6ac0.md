### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation accepts an arbitrary felt as the new class hash without verifying it corresponds to a declared class. This is the direct StarkNet analog of the OrchidLottery verifier-code-mutability bug: just as a verifier contract's code could be swapped out after validation, a StarkNet contract's class can be swapped to an undeclared (non-existent) hash, permanently bricking the contract and freezing any funds it holds. Via `library_call`, an unprivileged attacker can trigger this against a third-party contract.

---

### Finding Description

In `execute_replace_class`, the code explicitly acknowledges the missing check with a TODO:

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

Any felt value is accepted as `class_hash`. When a subsequent call is made to the contract, `execute_entry_point` performs:

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
``` [2](#0-1) 

Because `contract_class_changes` is initialized with default value `0` (via `dict_new()`), reading an undeclared class hash returns `0`. `find_element` is then called with `key=0`. Since no compiled class with hash `0` exists in the facts bundle, `find_element` raises an assertion failure, making the block unprovable. The sequencer must permanently reject all calls to the bricked contract.

**The `library_call` attack vector** is the critical unprivileged path. In `execute_library_call`, the execution context is constructed with `contract_address = caller_execution_info.contract_address`:

```cairo
tempvar execution_context: ExecutionContext* = new ExecutionContext(
    entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
    class_hash=request.class_hash,
    ...
    execution_info=new ExecutionInfo(
        ...
        contract_address=caller_execution_info.contract_address,
        ...
    ),
    ...
);
``` [3](#0-2) 

This means any `replace_class` call issued by the callee's code during a `library_call` targets the **caller's** contract address, not the callee's. An attacker who controls a class that is invoked via `library_call` by a victim contract can permanently brick the victim.

---

### Impact Explanation

**Critical. Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value in committed state, no entry point can ever be executed on it again. The sequencer rejects all calls (they would make the block unprovable). There is no recovery path: changing the class hash back requires executing an entry point, which is impossible. All funds held by the contract are permanently frozen.

---

### Likelihood Explanation

**Medium.** Two realistic paths exist:

1. **Direct self-bricking of a shared contract** (e.g., a multisig or DAO treasury): a malicious participant with execution rights calls `replace_class` with an undeclared hash, freezing all participants' funds.

2. **`library_call` attack on a third-party contract**: any contract that performs `library_call` with an attacker-controlled class hash (common in upgradeable proxy patterns or plugin architectures) can be bricked by the attacker's class calling `replace_class(undeclared_hash)`. This is reachable by an unprivileged transaction sender who can influence which class a victim contract uses for a library call.

---

### Recommendation

In `execute_replace_class`, before updating `contract_state_changes`, verify that `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry, meaning it was previously declared). A `dict_read` on `contract_class_changes` with the requested `class_hash` should return a non-zero value; if it returns `0`, the syscall should fail with an appropriate error response rather than committing the invalid state.

---

### Proof of Concept

**Step 1 — Attacker deploys a malicious class B** whose entry point calls `replace_class(0xdeadbeef_undeclared)`.

**Step 2 — Victim contract V** (holding user funds) performs a `library_call(class_B_hash, some_selector, ...)` — a realistic pattern in upgradeable proxies or DeFi plugin systems.

**Step 3 — During the `library_call`**, class B's code executes in V's context (`contract_address = V`). It issues `replace_class(0xdeadbeef_undeclared)`.

**Step 4 — `execute_replace_class`** writes `contract_state_changes[V].class_hash = 0xdeadbeef_undeclared` with no validation.

**Step 5 — Transaction succeeds** and the state change is committed.

**Step 6 — Any future call to V** reaches `execute_entry_point`, reads `compiled_class_hash = dict_read(contract_class_changes, 0xdeadbeef_undeclared) = 0`, then calls `find_element(..., key=0)` which panics. The sequencer permanently rejects all calls to V.

**Step 7 — All funds in V are permanently frozen.** [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L256-278)
```text
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
