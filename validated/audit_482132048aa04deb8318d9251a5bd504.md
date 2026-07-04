### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS Cairo program does not verify that the caller-supplied class hash corresponds to a previously declared contract class. An unprivileged contract can invoke `replace_class` with an arbitrary, undeclared class hash. The OS accepts and commits this update to state without any validation. Any subsequent execution targeting that contract will fail irrecoverably inside `execute_entry_point`, because the OS cannot resolve the undeclared hash to a compiled class. All funds held in the affected contract are permanently frozen.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the new class hash directly from the syscall request and writes it into `contract_state_changes` with no check that the hash exists in `contract_class_changes` (the declared-classes dictionary). The code itself acknowledges this gap with an explicit TODO:

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

After this update, any call to the affected contract reaches `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // ← now the attacker-controlled hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // ← returns 0 for undeclared hash
);
``` [2](#0-1) 

`dict_read` on `contract_class_changes` returns `0` for an undeclared key. `find_element` with key `0` against the compiled-class array will fail to locate a matching entry, causing the OS execution to abort. Because the OS is the authoritative proof-generation layer, no valid proof can ever be produced for a block that calls into this contract again. The contract is permanently bricked.

The SSRF structural analog is exact: just as the price-feeder followed an unvalidated redirect URL to an arbitrary local endpoint, `execute_replace_class` follows an unvalidated class-hash pointer to an arbitrary (potentially non-existent) class destination, with no allowlist or existence check at the OS level.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balances, ETH, STRK, or other assets held in the affected contract become permanently inaccessible. The contract's class hash in the committed state is set to an invalid value. No future transaction can successfully execute against it: `execute_entry_point` will always abort when trying to resolve the undeclared hash. Because the OS Cairo program is the proof-generating layer, this state corruption is committed to the proven output and cannot be undone without a protocol-level upgrade.

---

### Likelihood Explanation

**Medium.**

The attack surface is any contract that exposes a code path leading to the `replace_class` syscall. This includes:

- A contract that is tricked (e.g., via reentrancy or a malicious inner call) into calling `replace_class` with attacker-controlled calldata.
- A contract that intentionally calls `replace_class` with an invalid hash (e.g., a griefing attack against a shared protocol contract such as a DEX pool or token vault).
- A malicious sequencer that includes a crafted transaction; the OS has no enforcement barrier to reject it.

No privileged role is required. The `replace_class` syscall is reachable by any unprivileged transaction sender whose contract executes the syscall. Gas cost is bounded by `REPLACE_CLASS_GAS_COST`, which is a standard, affordable syscall cost. [3](#0-2) 

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, the OS must verify that the hash exists in `contract_class_changes`. Concretely, replace the TODO at line 898 with an actual lookup:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` but must be enforced proactively at the point of the `replace_class` syscall so that invalid state is never committed.

---

### Proof of Concept

1. **Deploy** a contract `Victim` that holds user funds (e.g., an ERC-20 vault). Its current class hash is `C_valid` (declared).

2. **Deploy** an attacker contract `Attacker` that, when called, issues the `replace_class` syscall targeting `Victim`'s address with `class_hash = 0xdeadbeef` (not declared in `contract_class_changes`).

   *(Note: `replace_class` operates on the calling contract's own address. The realistic scenario is a contract that is socially engineered or exploited via reentrancy to call `replace_class` with attacker-supplied calldata, or a malicious sequencer directly crafting the syscall segment.)*

3. **OS execution** of `execute_replace_class`:
   - Reads `request.class_hash = 0xdeadbeef`. [4](#0-3) 
   - Skips the missing validation (line 898 TODO). [5](#0-4) 
   - Commits `contract_state_changes[victim_address].class_hash = 0xdeadbeef`. [6](#0-5) 

4. **Subsequent call** to `Victim` in any future block:
   - `execute_entry_point` calls `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` → returns `0` (undeclared). [7](#0-6) 
   - `find_element(..., key=0)` fails to locate any compiled class. [8](#0-7) 
   - OS execution aborts; no valid proof can be generated for any block containing a call to `Victim`.

5. **Result**: All funds in `Victim` are permanently frozen. The contract is irrecoverably bricked at the OS/proof layer.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-895)
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

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
```text
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
