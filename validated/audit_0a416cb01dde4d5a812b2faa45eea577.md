### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` accepts any arbitrary class hash from a contract's syscall request without verifying that the hash corresponds to a declared contract class. An unprivileged contract caller can invoke `replace_class` with an undeclared class hash, permanently corrupting the contract's on-chain class hash. Any future call to that contract will cause the OS proof to fail at the `find_element` step (not a transaction revert), making the sequencer unable to include any transaction touching the contract and permanently freezing all funds held within it.

---

### Finding Description

In `execute_replace_class`, the new class hash is taken directly from the syscall request and written into `contract_state_changes` with no validation: [1](#0-0) 

The acknowledged TODO at line 898 confirms the missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The `class_hash` field is taken verbatim from `request.class_hash` — a value written by the calling contract — and immediately committed to `contract_state_changes` via `dict_update`. No lookup against `contract_class_changes` (the declared class registry) is performed.

When a future transaction calls the affected contract, `execute_entry_point` reads the corrupted class hash and attempts to resolve it: [2](#0-1) 

Step 1: `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` returns `0` (the default dict value) because the undeclared class hash was never registered.

Step 2: `find_element(..., key=0)` is called on `compiled_class_facts_bundle.compiled_class_facts`. Since no compiled class has hash `0`, the hint-assisted `find_element` cannot produce a valid witness, causing the entire OS proof to be invalid for any block containing a call to the affected contract.

The revert log does record the old class hash for potential rollback: [3](#0-2) 

However, this only applies if the *current* transaction reverts. If the `replace_class` call succeeds (which it always does — there is no failure path for an undeclared hash), the state change is committed permanently.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, the sequencer cannot produce a valid proof for any block that includes a call to that contract. The sequencer is forced to exclude all such transactions indefinitely. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path: the OS state is committed on-chain with the invalid class hash, and no future transaction can interact with the contract to withdraw funds.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract without OS-level access control. Realistic attack vectors include:

1. **Malicious escrow/proxy contract**: An attacker deploys a contract that accepts user deposits, then calls `replace_class` with an arbitrary undeclared hash, freezing all deposited funds.
2. **Vulnerable legitimate contract**: Any contract that exposes a `replace_class` code path with user-controlled input (e.g., via a callback or delegated call) can be exploited by an unprivileged transaction sender.

The attack requires only a standard contract call — no privileged role, no leaked key, no operator cooperation.

---

### Recommendation

In `execute_replace_class`, before committing the new class hash to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (the declared class registry). Specifically, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned compiled class hash is non-zero. If the class hash is undeclared, write a failure response via `write_failure_response` instead of updating the state.

---

### Proof of Concept

1. Attacker deploys `MaliciousContract` which holds user funds and contains:
   ```cairo
   fn freeze_self() {
       replace_class(class_hash: 0xdeadbeef);  // undeclared hash
   }
   ```
2. Users deposit funds into `MaliciousContract`.
3. Attacker calls `freeze_self()`. The OS processes `replace_class(0xdeadbeef)` via `execute_replace_class`. No validation occurs; `contract_state_changes` is updated with `class_hash = 0xdeadbeef`.
4. The transaction succeeds. The state is committed on-chain.
5. In any subsequent block, a user submits a withdrawal transaction calling `MaliciousContract`.
6. The OS calls `execute_entry_point`, reads `class_hash = 0xdeadbeef`, performs `dict_read` on `contract_class_changes` → returns `0`, then calls `find_element(..., key=0)` → no valid witness exists → proof generation fails.
7. The sequencer cannot include the withdrawal transaction. Funds are permanently frozen.

The root cause is the missing validation at: [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
