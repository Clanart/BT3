After thorough analysis of the scoped files, I identified a valid analog.

---

### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the replacement class hash corresponds to a previously declared class. An unprivileged contract deployer can call `replace_class` with an arbitrary, undeclared felt value as the new class hash. Once committed, any subsequent call to that contract causes the OS to panic during proof generation (because the undeclared hash cannot be resolved to a compiled class), permanently rendering the contract inaccessible and freezing any funds it holds.

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function updates `contract_state_changes` with the caller-supplied `class_hash` without any check that this hash exists in the declared class registry (`contract_class_changes`):

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

The acknowledged TODO confirms this check is intentionally absent. The `class_hash` field written here is later consumed by `execute_entry_point`, which performs:

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
``` [2](#0-1) 

If `class_hash` is an undeclared value, `dict_read` returns `0` (the default for uninitialized dict entries). `find_element` then searches for a compiled class with hash `0`. If none exists, the Cairo VM panics — the proof cannot be generated, and the sequencer cannot include any future transaction targeting that contract.

### Impact Explanation

**Critical. Permanent freezing of funds.**

Once a contract's class hash is overwritten with an undeclared value (Transaction T₁), every subsequent transaction that calls the contract (Transaction T₂, T₃, …) causes a Cairo VM panic inside `execute_entry_point` during sequencer execution. The sequencer cannot include these transactions in any block. The contract's storage — including all token balances or ETH-equivalent assets held there — becomes permanently inaccessible. There is no recovery path: the class hash is committed on-chain, and no transaction can reach the contract to restore it.

### Likelihood Explanation

**Medium.** The `replace_class` syscall is callable by any contract on itself — no privileged role is required. An attacker can:

- Deploy a contract that holds shared funds (e.g., a vault, multisig, or liquidity pool) and call `replace_class` with an arbitrary felt.
- Exploit any victim contract that exposes a function allowing an external caller to supply the class hash argument to `replace_class` (a pattern that exists in upgradeable contract designs).

The missing check is explicitly flagged with a `TODO` comment, confirming the developers are aware the guard is absent.

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, assert that the supplied `class_hash` resolves to a non-zero compiled class hash in `contract_class_changes`:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed in `execute_entry_point` and closes the gap noted by the TODO.

### Proof of Concept

1. **Attacker deploys** `VaultContract` holding user funds. The contract exposes:
   ```cairo
   @external
   func break_self{syscall_ptr: felt*}(new_hash: felt) {
       replace_class(class_hash=new_hash);
       return ();
   }
   ```

2. **Attacker calls** `break_self(new_hash=0xdeadbeef)` — an arbitrary felt that was never declared. The OS executes `execute_replace_class`: [3](#0-2) 
   No validation occurs. `contract_state_changes` is updated with `class_hash=0xdeadbeef`. Transaction is committed.

3. **Any user attempts** to call `VaultContract` (e.g., to withdraw funds). The sequencer runs `execute_entry_point`:
   <cite repo="blackvul/sequencer--010" path="crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo" start="

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-915)
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
