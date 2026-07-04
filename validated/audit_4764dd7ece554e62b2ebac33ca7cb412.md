### Title
`execute_replace_class` Does Not Validate That the New Class Hash Is Declared, Enabling Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS program accepts an arbitrary class hash from the syscall request and writes it directly into the contract's state entry without verifying that the class hash corresponds to a previously declared class. This is structurally identical to the reported bug: just as `bera_kodiakv2_swap` failed to validate the output token (causing assets to become stuck), `execute_replace_class` fails to validate the output class hash, allowing a contract to be permanently bricked and any funds it holds to be permanently frozen.

---

### Finding Description

In `execute_replace_class`, after reducing gas, the function reads `request.class_hash` and immediately writes it into `contract_state_changes` with no check against `contract_class_changes` (the dict that tracks declared classes):

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

The TODO comment at line 898 explicitly acknowledges the missing validation. [1](#0-0) 

By contrast, `execute_declare_transaction` in `transaction_impls.cairo` properly registers a class hash into `contract_class_changes` only after verifying the Sierra class hash pre-image via `finalize_class_hash`, and enforces `prev_value=0` to prevent double-declaration: [2](#0-1) 

The `execute_replace_class` function has access to `contract_class_changes` as an implicit argument in the broader `execute_syscalls` call graph, but `execute_replace_class` itself does not receive or consult it. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract calls `replace_class` with a class hash that has never been declared (i.e., has no entry in `contract_class_changes`), the OS proof accepts the state transition as valid. After this transition is finalized on L1:

- The contract's `class_hash` field in `contract_state_changes` points to a non-existent class.
- Every subsequent call to the contract will fail at entry-point resolution because no class bytecode exists for that hash.
- Any ERC-20 tokens, ETH, STRK, or other assets held in the contract's storage become permanently inaccessible — there is no recovery path, since the contract cannot execute any function (including a corrective `replace_class` call).

This matches the "permanent freezing of funds" impact category exactly.

---

### Likelihood Explanation

The attacker-controlled entry path is direct and requires no privileged role:

1. An unprivileged user deploys a contract whose code calls `replace_class(undeclared_hash)` — any felt value not present in `contract_class_changes` qualifies.
2. The user invokes that function via a standard `INVOKE_FUNCTION` transaction. [4](#0-3) 
3. `execute_syscalls` dispatches to `execute_replace_class` with no pre-check on the class hash. [5](#0-4) 
4. The OS proof is generated and verified on L1 with the invalid state transition embedded — because the OS never asserts `class_hash ∈ contract_class_changes`.

The sequencer's blockifier may independently reject such transactions today, but the OS proof program is the authoritative constraint enforcer for L1 verification. The absence of this check in the OS means the invariant is not cryptographically enforced, and a sequencer that omits or bypasses the blockifier-level check would produce a valid proof accepted by L1.

---

### Recommendation

Inside `execute_replace_class`, after reading `request.class_hash`, perform a `dict_read` on `contract_class_changes` to assert the class hash has a non-zero compiled class hash entry (mirroring the invariant enforced by `execute_declare_transaction`):

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,  // <-- add this implicit arg
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    ...
    let class_hash = request.class_hash;

    // Verify the class hash is declared.
    let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
    assert_not_zero(compiled_class_hash);

    // Proceed with state update.
    ...
}
```

This mirrors the validation present in `execute_declare_transaction` and closes the gap identified in the TODO comment. [6](#0-5) 

---

### Proof of Concept

```
1. Deploy ContractA with the following logic in its `__execute__` function:
       replace_class(0xdeadbeef)  // 0xdeadbeef is not declared

2. Fund ContractA with 100 STRK tokens.

3. Submit INVOKE_FUNCTION targeting ContractA.__execute__.

4. The OS processes the replace_class syscall via execute_replace_class.
   - request.class_hash = 0xdeadbeef
   - No dict_read on contract_class_changes is performed.
   - contract_state_changes[ContractA].class_hash is updated to 0xdeadbeef.

5. The OS proof is generated and verified on L1 — the proof is valid because
   the OS imposes no constraint linking class_hash to contract_class_changes.

6. Post-finalization: ContractA.class_hash = 0xdeadbeef (undeclared).
   Any call to ContractA fails at entry-point resolution.
   The 100 STRK in ContractA's storage are permanently frozen.
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions_inner.cairo (L39-43)
```text
    if (tx_type == 'INVOKE_FUNCTION') {
        // Handle the invoke-function transaction.
        execute_invoke_function_transaction(block_context=block_context);
        %{ ExitTx %}
        return execute_transactions_inner(block_context=block_context, n_txs=n_txs - 1);
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
