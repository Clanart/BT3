### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Arbitrary Class Hash Substitution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program does not verify that the caller-supplied `class_hash` corresponds to a previously declared class. A malicious prover can craft OS inputs that replace a contract's class with an undeclared hash, producing a proof the L1 verifier accepts. Any contract so modified becomes permanently unexecutable, permanently freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (the declared-class registry):

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

The developer-acknowledged TODO comment confirms the check is intentionally deferred but never enforced in the OS proof constraints. [1](#0-0) 

By contrast, `execute_entry_point` resolves a contract's compiled class by looking up `execution_context.class_hash` in `contract_class_changes` and then searching `compiled_class_facts_bundle` for the resulting compiled-class hash:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns 0, and `find_element` with key 0 will either trap (aborting OS execution) or, in a prover-crafted trace, silently resolve to a fabricated compiled-class pointer — both outcomes are catastrophic.

The analog to the external report is exact:

| External (SwapFacade) | Internal (StarkNet OS) |
|---|---|
| Any executor address accepted without allowlist | Any class hash accepted in `replace_class` without declared-class check |
| Fees charged on executor, not facade | State committed with unverified class hash |
| Users can choose buggy/forked executors | Prover can substitute arbitrary class for any contract |

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` in the committed state root points to an undeclared (and therefore unexecutable) class:

- Every subsequent call to that contract will fail at the OS level (no compiled class fact exists for the hash).
- The contract's storage and any token balances it holds become permanently inaccessible.
- Because the state root is committed to L1 via the accepted proof, the corruption is irreversible without a protocol-level upgrade.

---

### Likelihood Explanation

The StarkNet OS is a ZK proof system: its Cairo constraints are the sole trust anchor. The prover (sequencer) is explicitly the adversary the OS must constrain. The missing check means a malicious prover can include a `replace_class` operation with an arbitrary hash in a block, generate a valid STARK proof (the OS imposes no constraint on the hash), and have it accepted by the L1 verifier. No leaked key, phishing, or network-level attack is required — only the ability to produce OS inputs, which is the prover's normal role.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that it exists in `contract_class_changes` (i.e., has been declared in the current or a prior block):

```cairo
// Enforce that the replacement class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the check already performed implicitly in `execute_entry_point` and makes the OS constraint complete, closing the gap the TODO comment acknowledges. [3](#0-2) 

---

### Proof of Concept

1. **Setup**: Deploy contract `C` holding user funds. `C` implements a `replace_class` call in one of its entry points.

2. **Craft OS input**: The malicious prover includes a transaction that invokes `C`'s entry point. In the OS input, the `ReplaceClassRequest` for that syscall carries `class_hash = 0xdeadbeef` — a hash that has never appeared in any `DECLARE` transaction and therefore has no entry in `contract_class_changes`.

3. **OS execution**: `execute_replace_class` reads `class_hash = 0xdeadbeef`, finds no TODO-enforced check, and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.

4. **Proof generation**: The OS proof is valid — no Cairo constraint is violated. The STARK proof is submitted to L1.

5. **L1 acceptance**: The L1 verifier accepts the proof. The new state root encodes `C.class_hash = 0xdeadbeef`.

6. **Permanent freeze**: Any future call to `C` reaches `execute_entry_point`, which does `dict_read(key=0xdeadbeef)` → returns 0, then `find_element(key=0)` → no compiled class fact exists → execution fails unconditionally. All funds in `C` are permanently frozen. [4](#0-3) [5](#0-4)

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
