### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS processes the `replace_class` syscall without verifying that the supplied class hash has ever been declared. This decouples the two operations that must be sequenced — `declare_transaction` (which registers a class in `contract_class_changes`) and `replace_class` (which updates a contract's live class hash) — in exact analogy to the PCVEquityMinter/BalancerLBPSwapper desynchronisation. A contract can therefore replace its own class hash with an arbitrary, undeclared felt value. Because the OS commits this corrupted state to the Patricia Merkle Tree and generates a valid proof, the contract is permanently unexecutable and any funds it holds are permanently frozen.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` performs the following steps:

1. Deducts gas.
2. Reads `request.class_hash` — a caller-controlled felt.
3. Fetches the current `StateEntry` for the contract.
4. Writes a new `StateEntry` with the caller-supplied `class_hash` into `contract_state_changes`.
5. Appends a revert-log entry. [1](#0-0) 

The critical missing step — explicitly flagged by the codebase itself — is:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [2](#0-1) 

No `dict_read` against `contract_class_changes` is performed to confirm the hash exists. The OS therefore accepts the state transition unconditionally.

The intended two-step sequence is:

| Step | Operation | Enforced? |
|------|-----------|-----------|
| 1 | `declare_transaction` → inserts `class_hash → compiled_class_hash` into `contract_class_changes` | ✅ enforced |
| 2 | `replace_class` → updates contract's live `class_hash` | ❌ **not checked against step 1** |

This is structurally identical to the reported bug: `BalancerLBPSwapper.swap()` could be called without the preceding `PCVEquityMinter.mint()` because the coupling between the two was not enforced.

When a future transaction attempts to call the bricked contract, `execute_entry_point` reads the class hash from state and calls `dict_read` on `contract_class_changes`: [3](#0-2) 

Because the undeclared hash has no entry, `compiled_class_hash` resolves to 0. The subsequent `find_element` call over the compiled class facts bundle cannot locate a fact with key 0, causing the OS to fail for every future invocation of that contract. The state corruption is committed to the Merkle tree and proven to L1 — it is irreversible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared hash, the contract's class hash in the global state root is set to an arbitrary felt. No future transaction can successfully execute against that contract address. Any ERC-20 balances, vault deposits, or other assets held by the contract are permanently inaccessible. The corrupted state is committed to L1 via a valid STARK proof, so there is no on-chain mechanism to recover the funds.

---

### Likelihood Explanation

The attack requires only that a contract's code contain a `replace_class` syscall with an attacker-controlled or hardcoded undeclared hash. This is reachable by:

- A contract owner who deploys a contract containing such a call (e.g., a malicious upgrade path in a shared vault or proxy), then invokes it after users have deposited funds.
- Any contract that exposes `replace_class` with calldata-derived arguments, allowing an unprivileged transaction sender to supply an undeclared hash.

No privileged sequencer action is required beyond normal transaction inclusion. The OS itself produces a valid proof for the corrupted state transition.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, add a `dict_read` against `contract_class_changes` to confirm the supplied class hash maps to a non-zero compiled class hash:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
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
