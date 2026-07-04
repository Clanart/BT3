### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. Any contract can call `replace_class` with an arbitrary, undeclared class hash. Once committed to state, the contract becomes permanently uncallable at the OS proof level, permanently freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts the caller-supplied `class_hash` from the syscall request and writes it directly into `contract_state_changes` without any validation that the hash corresponds to a declared class:

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

The TODO comment at line 898 explicitly acknowledges this missing check. The syscall succeeds and the state change is committed with no enforcement that `class_hash` is a valid, declared class.

When any subsequent call is made to the now-bricked contract, `execute_entry_point` performs:

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

If the class hash stored in state was never declared, `dict_read` returns 0 (the default uninitialized value), and `find_element` — which asserts the element exists — will fail, making any block containing a call to the bricked contract unprovable. The contract is permanently uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value, the contract is permanently bricked at the OS proof layer. No valid proof can be generated for any block that includes a call to that contract. All ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path: the state change is committed on-chain and cannot be reversed without a protocol-level intervention.

---

### Likelihood Explanation

**Medium.** The `replace_class` syscall is callable by any contract on itself. The attack surface includes:

1. **Malicious contract deployers**: A deployer creates a contract that accepts user deposits, then triggers `replace_class(undeclared_hash)` to permanently freeze deposited funds (a rug-pull variant with no recovery).
2. **Vulnerable existing contracts**: Any contract with a logic bug that allows an unprivileged caller to trigger `replace_class` with an attacker-controlled argument is exploitable. The OS provides no safety net.

The TODO comment at line 898 confirms the Primitive team is aware this check is absent, increasing the likelihood that it will be discovered and exploited before it is fixed.

---

### Recommendation

In `execute_replace_class`, before committing the state update, verify that `class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` when executing a call, but must be enforced eagerly at the point of `replace_class` to prevent the state from ever reaching an unprovable configuration.

---

### Proof of Concept

1. **Attacker deploys** a contract `VaultContract` that:
   - Accepts user deposits (stores balances in storage).
   - Exposes a function `brick()` that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.

2. **Users deposit funds** into `VaultContract`.

3. **Attacker calls** `brick()`. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is written to `contract_state_changes[VaultContract]`.
   - No validation occurs. The transaction succeeds and is included in a block.

4. **Any subsequent call** to `VaultContract` (e.g., `withdraw`) reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(compiled_class_facts, 0)` → **assertion failure**: no compiled class with hash `0` exists.
   - The block containing this call is **unprovable**.

5. The sequencer must permanently exclude all calls to `VaultContract`. All user funds are **permanently frozen** with no recovery mechanism.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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
