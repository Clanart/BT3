### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo`)

---

### Summary

The `execute_replace_class` function in `deprecated_execute_syscalls.cairo` updates a contract's `class_hash` in the OS state without verifying that the new hash is actually declared in `contract_class_changes`. Any unprivileged user can deploy a deprecated contract and invoke `replace_class` with an arbitrary, undeclared class hash, permanently breaking the contract and freezing any funds it holds.

---

### Finding Description

The vulnerability class from M-06 is a **registry-bypass**: a state-mutating function that should only produce registered/validated state instead accepts arbitrary input and writes it directly, creating inconsistent state that later breaks dependent functionality.

The direct analog in this codebase is `execute_replace_class` in the deprecated syscall handler.

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;          // ← attacker-controlled

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash,                         // ← written unconditionally
        storage_ptr=state_entry.storage_ptr,
        nonce=state_entry.nonce,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    ...
    return ();
}
``` [1](#0-0) 

The function reads `class_hash` directly from the syscall pointer (attacker-controlled calldata) and writes it into `contract_state_changes` with **no assertion** that `class_hash` exists as a key in `contract_class_changes` (i.e., that it was ever declared via a declare transaction).

Compare this with the normal declare path, which enforces `prev_value=0` and requires a valid `compiled_class_hash`:

```cairo
// Declare the class hash.
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

The declare path enforces that a class hash maps to a real compiled class. `execute_replace_class` bypasses this entirely, writing an arbitrary felt into the contract's `class_hash` field with no corresponding entry in `contract_class_changes`.

When the OS later executes a call to that contract, it must look up the compiled class for the stored `class_hash` in `compiled_class_facts`. Because no such entry exists, the OS cannot produce a valid execution trace for that contract. The contract is permanently unexecutable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to an undeclared value, every future transaction that attempts to call that contract will fail at the OS level (the compiled class cannot be found in `compiled_class_facts`). The contract becomes permanently inert. Any ERC-20 balances, NFTs, or other assets held by the contract are irrecoverably frozen. The attacker does not need to hold any privileged role; they only need to deploy a deprecated contract and invoke the `replace_class` syscall.

---

### Likelihood Explanation

The attack path is fully reachable by any unprivileged user:

1. Deploy a deprecated (v0/v1) contract using any declared class.
2. From within that contract, emit a `ReplaceClass` syscall with an arbitrary felt as the new `class_hash` (e.g., `0xdeadbeef`, which has no corresponding compiled class).
3. The OS processes the syscall through `execute_replace_class`, which writes the arbitrary hash into `contract_state_changes` without validation.
4. The contract's on-chain `class_hash` is now set to the undeclared value.
5. Any subsequent call to the contract fails permanently.

No special permissions, leaked keys, or sequencer compromise are required. The deprecated execution path is still live and reachable via v0/v1 invoke transactions.

---

### Recommendation

Add an explicit check inside `execute_replace_class` that the new `class_hash` is present in `contract_class_changes` before writing it to `contract_state_changes`. This mirrors the invariant enforced by the declare transaction path and closes the registry-bypass gap.

Concretely, perform a `dict_read` on `contract_class_changes` for the new `class_hash` and assert the returned value is non-zero (i.e., a compiled class hash was previously registered for it) before executing the `dict_update` on `contract_state_changes`.

---

### Proof of Concept

```
1. Attacker deploys contract A using a legitimately declared class (class_hash = H_valid).
   → contract_state_changes[A].class_hash = H_valid
   → contract_class_changes[H_valid] = compiled_hash  (exists)

2. Attacker calls contract A, which internally executes:
       replace_class(class_hash=0xdeadbeef)

3. OS dispatches to execute_replace_class:
       class_hash = 0xdeadbeef  (from syscall_ptr.class_hash)
       // No check: is 0xdeadbeef in contract_class_changes? ← MISSING
       dict_update(contract_state_changes, key=A,
                   prev=H_valid, new=0xdeadbeef)   ← accepted

4. Block is proven. On-chain state now has:
       contract_state_changes[A].class_hash = 0xdeadbeef
       contract_class_changes[0xdeadbeef]  = (does not exist)

5. In any future block, a transaction calls contract A.
   OS looks up compiled_class_facts[0xdeadbeef] → not found.
   OS cannot generate a valid execution trace for contract A.
   Contract A is permanently unexecutable.

6. All funds held by contract A are permanently frozen.
``` [1](#0-0) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-328)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/compiled_class.cairo (L99-138)
```text
func validate_compiled_class_facts{poseidon_ptr: PoseidonBuiltin*, range_check_ptr}(
    n_compiled_class_facts, compiled_class_facts: CompiledClassFact*, builtin_costs: felt*
) {
    if (n_compiled_class_facts == 0) {
        return ();
    }
    alloc_locals;

    let compiled_class_fact = &compiled_class_facts[0];
    let compiled_class = compiled_class_fact.compiled_class;

    validate_entry_points(
        n_entry_points=compiled_class.n_external_functions,
        entry_points=compiled_class.external_functions,
    );

    validate_entry_points(
        n_entry_points=compiled_class.n_l1_handlers, entry_points=compiled_class.l1_handlers
    );
    // Compiled classes are expected to end with a `ret` opcode followed by a pointer to the
    // builtin costs.
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length] = 0x208b7fff7fff7ffe;
    assert compiled_class.bytecode_ptr[compiled_class.bytecode_length + 1] = cast(
        builtin_costs, felt
    );

    // Calculate the compiled class hash.
    // This hint enters a new scope that contains the bytecode segment structure of the class.
    %{ EnterScopeWithBytecodeSegmentStructure %}
    let (hash) = blake_compiled_class_hash(compiled_class, full_contract=FALSE);
    %{ LoadClass %}

    assert compiled_class_fact.hash = hash;

    return validate_compiled_class_facts(
        n_compiled_class_facts=n_compiled_class_facts - 1,
        compiled_class_facts=&compiled_class_facts[1],
        builtin_costs=builtin_costs,
    );
}
```
