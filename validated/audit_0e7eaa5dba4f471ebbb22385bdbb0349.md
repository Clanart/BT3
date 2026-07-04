### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by a contract corresponds to a previously declared contract class before committing the irreversible state update. This is the direct structural analog of the audited report: an irreversible destructive action (overwriting the contract's class pointer) is performed without first confirming the destination (the new class) is valid and reachable. Any funds held by a contract whose class hash is replaced with an undeclared hash are permanently frozen, with no recovery path.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the caller-supplied `class_hash` from the syscall request and immediately writes it into `contract_state_changes` without checking whether that hash exists in `contract_class_changes` (i.e., whether it was ever declared):

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

The developer-authored `TODO` comment at line 898 explicitly acknowledges the missing check. The identical omission exists in the deprecated path (`deprecated_execute_syscalls.cairo`, `execute_replace_class`).

Once `dict_update` commits the new `StateEntry` with an undeclared `class_hash`, every future call to that contract address will fail at entry-point resolution (no compiled class exists for the hash). The state transition is final: the block is proven and committed to L1 with this broken state, and there is no on-chain mechanism to undo it. [1](#0-0) 

The deprecated counterpart has the same structural gap: [2](#0-1) 

---

### Impact Explanation

**Impact: Critical — Permanent freezing of funds.**

After a successful `replace_class` to an undeclared class hash:

- The contract's `class_hash` field in the committed state tree points to a hash with no associated compiled class.
- Every subsequent `call_contract`, `library_call`, or L1-handler invocation targeting that address fails at entry-point lookup.
- ERC-20 balances, ETH, or any other assets stored in the contract's storage slots become permanently inaccessible — there is no `upgrade`, `withdraw`, or `rescue` path because no code can execute.
- The proof is generated and verified against this broken state; L1 accepts it. The damage is irreversible at the protocol level.

---

### Likelihood Explanation

The entry path requires a contract to call `replace_class` with an attacker-controlled or erroneous class hash. This is reachable in at least two realistic scenarios:

1. **Direct self-destruction**: A contract owner (unprivileged deployer) calls `replace_class` with a hash that was never declared (e.g., a typo, a hash of a class that failed declaration, or a deliberately crafted garbage value). The OS accepts it without error.

2. **Attacker-triggered via vulnerable contract**: A contract that exposes an upgradeable pattern where the new class hash is taken from calldata or storage without internal validation can be exploited by an unprivileged caller to supply an undeclared hash, permanently freezing the contract's funds. The OS is the last line of defense and provides none.

The `TODO` comment confirms the development team is aware this check is absent and deferred it, meaning the gap is present in the current production code path.

---

### Recommendation

Before committing the `dict_update` in `execute_replace_class`, assert that `class_hash` exists in `contract_class_changes` (or the global declared-class set). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled-class hash is non-zero, mirroring the pattern already used in `execute_call_contract` where `state_entry.class_hash` is read and trusted only because it was previously written by a validated declare transaction. [3](#0-2) 

---

### Proof of Concept

1. Declare class `A` (valid, deployed contract holding 1000 STRK).
2. Construct `class_hash_X = 0xdeadbeef...` — a felt value that was never passed through a `declare` transaction and therefore has no entry in `contract_class_changes`.
3. From within contract `A`, invoke the `replace_class` syscall with `class_hash = class_hash_X`.
4. The OS executes `execute_replace_class`:
   - Reads `request.class_hash = class_hash_X`. [4](#0-3) 
   - Skips any existence check (the `TODO` line). [5](#0-4) 
   - Writes `StateEntry(class_hash=class_hash_X, ...)` into `contract_state_changes`. [6](#0-5) 
5. Block is proven and committed to L1 with contract `A`'s class hash set to `class_hash_X`.
6. Any subsequent call to contract `A` fails: no compiled class for `class_hash_X` exists. The 1000 STRK are permanently frozen.

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
