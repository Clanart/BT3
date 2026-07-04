### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash has been declared. Any contract can replace its own class hash with an arbitrary, undeclared value. Once this happens, all future calls to that contract cause an irrecoverable proof failure, permanently freezing any funds held by the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function contains an explicit TODO acknowledging the missing check:

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

The OS unconditionally writes the caller-supplied `class_hash` into `contract_state_changes` without consulting `contract_class_changes` to confirm the hash is declared.

When any subsequent call targets that contract, `execute_entry_point` performs:

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

For an undeclared class hash, `dict_read` returns the default value `0`. `find_element` then searches for a compiled class with hash `0`. Because no such class exists, the Cairo assertion inside `find_element` fails, making the proof invalid. The sequencer cannot include any transaction that calls the affected contract in a provable block. The contract becomes permanently unreachable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds (ETH, STRK, or ERC-20 tokens) held in a contract whose class hash has been replaced with an undeclared value are permanently inaccessible. No withdrawal, transfer, or recovery transaction can be included in a valid block, because every such call causes an irrecoverable proof failure at the OS level. This matches the "permanent freezing of funds" critical impact category.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context — no privileged role is required. Realistic attack vectors include:

1. A malicious contract deployer attracts user deposits, then calls `replace_class` with an arbitrary undeclared hash, freezing all deposited funds.
2. An attacker exploits a reentrancy or logic bug in an upgradeable DeFi contract to trigger `replace_class` with an undeclared hash, freezing the protocol's treasury.

The OS is the necessary vulnerable step: it is the only component that enforces (or fails to enforce) the invariant that a contract's class hash must be declared.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes`:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,  // add implicit arg
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    // ...
    let class_hash = request.class_hash;

    // Verify the class is declared.
    let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
    with_attr error_message("Class hash is not declared.") {
        assert_not_zero(compiled_class_hash);
    }
    // ... rest of the function
}
```

This mirrors the check already performed implicitly in `execute_entry_point` and makes the invariant explicit and enforced at the point of mutation.

---

### Proof of Concept

1. Attacker deploys contract `C` (class hash `H_valid`, declared). Users deposit 1000 STRK into `C`.
2. Attacker calls a function in `C` that invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (never declared).
3. The OS executes `execute_replace_class`: no declared-class check exists, so `contract_state_changes[C].class_hash` is updated to `0xdeadbeef`. The transaction is included in the block and proved.
4. A user submits a withdrawal transaction targeting `C`.
5. The sequencer attempts to build a proof: `execute_entry_point` calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`; `find_element(..., key=0)` → assertion failure; proof is invalid.
6. The sequencer cannot include the withdrawal. No valid proof can ever be generated for any call to `C`. The 1000 STRK are permanently frozen. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L142-167)
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
```
