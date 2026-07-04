### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS applies a class hash state mutation without verifying that the new class hash corresponds to a declared contract class. An attacker can replace a contract's class with an arbitrary, undeclared felt value, permanently bricking the contract and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash exists in `contract_class_changes`:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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

The `contract_class_changes` dictionary — which tracks declared classes — is not consulted at all. Any arbitrary felt value (e.g., `0xdeadbeef`) is accepted as a valid replacement class hash and committed to state.

This is the direct analog to the external report's root cause: **a state mutation (class replacement) is applied without the required validity check (class existence)**, mirroring how the Derive protocol transferred assets before completing the risk check.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Attack scenario:

1. Attacker deploys a contract (Contract A) that presents a legitimate interface (e.g., a vault or yield aggregator) and attracts user deposits of ETH/STRK.
2. The attacker's contract contains a hidden function that:
   - Drains attacker-owned funds or performs any desired action, then
   - Calls `replace_class` with an arbitrary undeclared class hash (e.g., `1`).
3. The OS writes the invalid class hash into `contract_state_changes` for Contract A with no validation.
4. All subsequent calls to Contract A attempt to execute a class that does not exist in `contract_class_changes`. The OS cannot resolve the entry point, causing every call to revert.
5. All user funds deposited into Contract A are permanently inaccessible — no withdrawal, no recovery, no upgrade path.

The funds are frozen at the protocol level: the OS state records a class hash that has no corresponding bytecode, so no entry point of Contract A can ever execute again.

---

### Likelihood Explanation

**High.** The `replace_class` syscall is a standard, documented StarkNet syscall callable by any contract. No privileged role, operator key, or special permission is required. Any unprivileged transaction sender who deploys a contract can invoke this syscall with an arbitrary class hash. The missing check is a single-line omission explicitly flagged by the development team's own TODO comment, confirming awareness of the gap. The attack requires only deploying a contract and submitting a transaction — both are public protocol entry points.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the requested class hash exists in `contract_class_changes`. Specifically, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero (i.e., a compiled class hash has been registered for it). This mirrors the validation that `execute_declare_transaction` enforces when registering a new class:

```cairo
// After: let class_hash = request.class_hash;
// Add:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // Revert if class is not declared.
```

This ensures `replace_class` can only target classes that have been legitimately declared on-chain, closing the state-mutation-without-check gap.

---

### Proof of Concept

1. Declare a valid contract class (Class V) on-chain — this is the contract users will interact with.
2. Deploy Contract A using Class V. Users deposit funds into Contract A.
3. Submit an invoke transaction that calls a function in Contract A which executes:
   ```
   replace_class(class_hash=0x1)  // 0x1 is not a declared class hash
   ```
4. The OS processes `execute_replace_class`:
   - Reads `class_hash = 0x1` from the syscall request.
   - Skips the (missing) existence check.
   - Writes `StateEntry(class_hash=0x1, ...)` into `contract_state_changes` for Contract A.
   - Transaction succeeds; state is committed.
5. Any subsequent `call_contract` targeting Contract A reads `class_hash=0x1` from state, attempts to look up the class, finds nothing, and reverts.
6. All funds in Contract A are permanently frozen with no recovery path.

**Relevant code locations:** [1](#0-0) 

The missing `contract_class_changes` lookup is the root cause — `class_hash` from the request is written directly to state: [2](#0-1) 

The `execute_call_contract` function shows how the corrupted class hash propagates to execution failure — the invalid hash is read from state and used to construct the execution context with no further validation: [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L192-215)
```text
    tempvar contract_address = request.contract_address;
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Prepare execution context.
    // TODO(Yoni, 1/1/2026): change ExecutionContext to hold calldata_start, calldata_end.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar caller_address = caller_execution_info.contract_address;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );
```

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
