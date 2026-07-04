### Title
`execute_replace_class` Does Not Verify the New Class Hash Is Declared — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS processes the `replace_class` syscall and updates a contract's class hash in state without verifying that the supplied class hash corresponds to a previously declared class. An explicit `TODO` comment in the code acknowledges this missing check. A malicious or buggy contract can exploit this to set its class hash to an arbitrary, undeclared value, permanently bricking the contract and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` from the syscall request and immediately writes it to `contract_state_changes` without consulting `contract_class_changes` to confirm the hash is a declared class:

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

The same omission exists in the deprecated path in `deprecated_execute_syscalls.cairo` at `execute_replace_class` (lines 307–329), which also writes the new class hash directly to state with no declaration check.

The `contract_class_changes` dictionary — which tracks class hash → compiled class hash mappings for classes declared in the current block — is never consulted. The OS therefore accepts and commits any arbitrary felt as a new class hash.

After the state update is committed, any subsequent call to the affected contract requires the OS to resolve the class hash to a compiled class. Because the hash is undeclared, no valid compiled class facts exist for it. The contract becomes permanently unexecutable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is finalized on L1, the state transition is irreversible. Every future call to that contract will fail at class resolution time. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible. No recovery path exists at the protocol level.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself. The attack requires only:

1. Deploying a contract (permissionless).
2. Attracting user deposits (social engineering or legitimate service).
3. Issuing a `replace_class` syscall with an arbitrary, undeclared felt as the class hash.

The OS Cairo code is the authoritative source of truth for the STARK proof. Because the OS does not enforce the declaration check, a sequencer that includes such a transaction produces a valid proof. The missing check is not compensated for anywhere else in the scoped OS code. The `TODO` comment confirms the check is intentionally absent and deferred.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was declared in the current block) **or** that it already exists in the pre-block class trie (via a hint-backed read). Concretely, add a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero before proceeding with the state update. Apply the same fix to the deprecated path in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Attacker deploys contract `V` that implements a token vault accepting user deposits.
2. Users deposit funds; `V` accumulates a balance.
3. Attacker calls `V.__execute__` with a transaction that internally issues `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is never declared.
4. The OS executes `execute_replace_class`:
   - Gas is deducted.
   - `contract_state_changes[V].class_hash` is set to `0xdeadbeef`.
   - No check against `contract_class_changes` is performed.
   - The revert log records the old class hash.
5. The block is proven and finalized on L1 with this state transition.
6. Any subsequent `call_contract` or `invoke` targeting `V` causes the OS to look up compiled class facts for `0xdeadbeef`, finds none, and the execution fails.
7. All user funds in `V` are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1)

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
