### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program does not verify that the new class hash supplied by a contract is actually declared in `contract_class_changes` before committing the state update. This is directly analogous to the external report's "dead gauge" pattern: a missing liveness/validity check on an entity before processing it leads to incorrect committed state. A malicious sequencer (or a sequencer that does not independently enforce this check) can cause a contract's class hash to be set to an undeclared value, permanently rendering the contract uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash from the syscall request and immediately writes it into `contract_state_changes` without verifying that the class hash exists in `contract_class_changes` (i.e., that it was previously declared):

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
```

The developer-acknowledged TODO at line 898 confirms this check is intentionally deferred but not yet implemented. [1](#0-0) 

The same missing check exists in the deprecated syscall path: [2](#0-1) 

The OS enforces class hash validity only at execution time, inside `execute_entry_point`, where it performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [3](#0-2) 

If `class_hash` is undeclared, `compiled_class_hash` resolves to `UNINITIALIZED_CLASS_HASH` (0), and `find_element` will fail to locate a matching compiled class fact. However, this failure only manifests when the contract is subsequently called — not at the point of the `replace_class` syscall itself. The state commitment is already written with the invalid class hash.

The `UNINITIALIZED_CLASS_HASH` constant is defined as `0`: [4](#0-3) 

---

### Impact Explanation

Once a contract's class hash is set to an undeclared value and the block is proven and finalized:

1. The contract's `StateEntry.class_hash` in the global state tree permanently points to a non-existent class.
2. Any future call to the contract will fail at the OS level when `find_element` cannot locate the compiled class — the sequencer will revert all such calls.
3. No entry point of the contract (including withdrawal functions) can ever execute successfully again.
4. All ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The OS is the cryptographic enforcement layer. Because the OS does not enforce the declared-class-hash check, a malicious or compromised sequencer can include a `replace_class` syscall with an arbitrary undeclared class hash, mark the transaction as successful (not reverted), and generate a valid STARK proof that the verifier will accept. The attacker-controlled entry path is:

1. Attacker deploys a contract (unprivileged deployer).
2. Attacker submits a transaction that calls `replace_class` with an undeclared class hash.
3. A malicious sequencer processes this as a successful (non-reverted) transaction.
4. The OS Cairo program generates a valid proof — it does not check class hash validity in `execute_replace_class`.
5. The on-chain verifier accepts the proof; the state root is updated with the broken contract.
6. Funds are permanently frozen.

The sequencer is the only actor that currently enforces this check at the application layer, but the OS — which is supposed to be the trustless enforcement layer — does not. This gap is explicitly acknowledged in the source code.

**Likelihood: Medium** — requires a malicious or compromised sequencer, but the OS provides no cryptographic guarantee against this attack.

---

### Recommendation

Before committing the `dict_update` in `execute_replace_class`, verify that the requested class hash exists in `contract_class_changes` (i.e., its value is non-zero / not `UNINITIALIZED_CLASS_HASH`). This mirrors the check already present in `execute_declare_transaction`, which uses `prev_value=0` to enforce that a class may only be declared once:

```cairo
// In execute_replace_class, after reading class_hash:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // Enforce class is declared.
```

Apply the same fix to the deprecated `execute_replace_class` in `deprecated_execute_syscalls.cairo`. [5](#0-4) 

---

### Proof of Concept

1. Attacker deploys `VictimContract` holding user funds, using a valid declared class hash `C_valid`.
2. Attacker submits an invoke transaction that calls `replace_class(class_hash=0xDEAD)` where `0xDEAD` is never declared.
3. A malicious sequencer marks this transaction as successful (not reverted).
4. The OS processes `execute_replace_class`:
   - `class_hash = 0xDEAD` (line 896)
   - No check against `contract_class_changes` (line 898 TODO)
   - `dict_update` writes `StateEntry(class_hash=0xDEAD, ...)` for `VictimContract` (lines 906–910)
5. The OS generates a valid STARK proof; the on-chain verifier accepts it.
6. `VictimContract`'s class hash in the global state is now `0xDEAD`.
7. Any future call to `VictimContract` reaches `execute_entry_point`, which reads `compiled_class_hash = dict_read(key=0xDEAD) = 0`, then `find_element` fails to locate a compiled class with hash `0` — the call reverts.
8. All funds in `VictimContract` are permanently frozen with no recovery path.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
