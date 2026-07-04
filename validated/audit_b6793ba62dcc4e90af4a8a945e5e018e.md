### Title
Missing Class Hash Existence Validation in `replace_class` Syscall Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that a compiled class with that hash has been declared. An unprivileged contract deployer can call `replace_class` with a non-existent class hash, permanently rendering the contract uncallable and freezing any funds held within it.

---

### Finding Description

In `execute_replace_class`, the new `class_hash` from the request is written directly into `contract_state_changes` with no check that the hash corresponds to a declared (and compiled) class:

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
``` [1](#0-0) 

The developer-acknowledged TODO confirms the missing guard. Once the contract's `class_hash` field is set to an undeclared value, any subsequent call to that contract reaches `execute_entry_point`, which performs:

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

`dict_read` returns `0` for an undeclared class hash (the dict default). `find_element` then panics at the OS level when no compiled class with hash `0` exists. Because this is an OS-level panic (not a transaction revert), the sequencer cannot include any future transaction that calls the affected contract — the contract is permanently dead.

The only mechanism to recover a contract's class hash is `replace_class` itself, which requires calling the contract — an impossibility once the class hash is invalid.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balances, ETH bridged via L1→L2, or protocol TVL held in a contract whose class hash has been poisoned with an undeclared value becomes permanently inaccessible. There is no admin escape hatch, no governance override, and no upgrade path once the state transition is finalized on-chain.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. Deploying (or controlling) a contract — a standard, permissionless operation.
2. Invoking a function that calls `replace_class` with an arbitrary felt (e.g., `0xdead`).

No privileged role, leaked key, or external dependency is needed. The `replace_class` syscall is available to every Cairo contract. A malicious actor can deploy a contract that appears to be a legitimate DeFi vault, attract user deposits, and then call `replace_class` with a garbage hash to permanently freeze all deposited funds.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that the hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` but must be enforced eagerly at the point of `replace_class` to prevent the contract from entering an unrecoverable state.

---

### Proof of Concept

1. Declare a legitimate class `C` and deploy contract `V` (a "vault") using class `C`.
2. Users deposit tokens into `V`; `V` now holds funds.
3. Attacker calls a function on `V` (or a separate attacker-controlled contract that `V` delegates to) that issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (undeclared).
4. The OS executes `execute_replace_class`: no validation fires, `contract_state_changes` is updated with `class_hash = 0xdeadbeef` for `V`'s address. The transaction is included in the block and finalized.
5. Any subsequent transaction targeting `V` causes `execute_entry_point` to call `dict_read` → returns `0` → `find_element` panics → the sequencer cannot prove any block containing a call to `V`.
6. All funds in `V` are permanently frozen. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L142-177)
```text
func execute_entry_point{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```
