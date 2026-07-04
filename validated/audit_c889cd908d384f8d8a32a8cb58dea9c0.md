### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by a contract corresponds to a previously declared class. Any contract can call `replace_class` with an arbitrary undeclared felt value, permanently corrupting its own on-chain class-hash entry. Because the OS proof-generation path will subsequently fail to resolve that hash to a compiled class, any funds held by the contract become permanently frozen, and any block that includes a call to the bricked contract will fail to prove, threatening a network halt.

---

### Finding Description

In `execute_replace_class` the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no check that the hash is declared:

```cairo
// syscall_impls.cairo  lines 896-910
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

The in-code `TODO` comment explicitly acknowledges the missing guard. The state update is committed to the Merkle tree and ultimately to L1 as part of the block that contains the `replace_class` call; that block itself is valid because the OS does not fail during `replace_class` processing.

When any subsequent block attempts to execute the now-bricked contract, `execute_entry_point` performs:

```cairo
// execute_entry_point.cairo  lines 154-166
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash          // ← the undeclared hash X
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,                  // ← 0, because X is not declared
);
```

Because hash `X` is absent from `contract_class_changes`, `dict_read` returns `0`. `find_element` then searches for a compiled class with key `0`; no such entry exists, so the Cairo assertion inside `find_element` fails, aborting the entire OS execution and making it impossible to generate a valid proof for that block.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

The corrupted class-hash entry is written to the global state Merkle tree and settled on L1 as part of the block that contained the `replace_class` call. Once settled:

- Every future call to the contract causes the OS to abort proof generation.
- The contract can never execute `replace_class` again to self-repair (execution is impossible).
- Any ERC-20 balances, ETH, or other assets held in the contract's storage are permanently inaccessible.
- No on-chain mechanism exists to overwrite the class hash without a protocol-level emergency upgrade.

**High — Network not being able to confirm new transactions.**

If the sequencer's blockifier does not independently enforce the same declared-class constraint (consistent with the OS-level TODO), the sequencer may include calls to the bricked contract in subsequent blocks. Each such block will fail to prove, stalling block finalization until the sequencer detects and filters out all interactions with the affected contract.

---

### Likelihood Explanation

The attack requires only two standard, unprivileged on-chain actions:

1. Deploy a contract (any user can do this).
2. From that contract, emit a `REPLACE_CLASS` syscall with an arbitrary felt as the new class hash.

No privileged role, leaked key, or operator cooperation is needed. The `execute_replace_class` handler is reachable by any deployed contract via the normal syscall dispatch path in `execute_syscalls`. The explicit `TODO` comment confirms the validation gap is known and unresolved in the current codebase.

---

### Recommendation

Inside `execute_replace_class`, before performing `dict_update`, verify that `class_hash` is present in `contract_class_changes` (i.e., was declared in the current or a prior block). Concretely:

```cairo
// After reading class_hash from the request:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // revert if class is not declared
```

This mirrors the check already performed implicitly in `execute_entry_point` and closes the gap between what the OS enforces and what the protocol requires.

---

### Proof of Concept

**Block N (attack setup):**

1. Attacker deploys contract `C` at address `addr` with a legitimate class hash.
2. Contract `C` executes the `REPLACE_CLASS` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
3. `execute_replace_class` writes `contract_state_changes[addr].class_hash = 0xdeadbeef` with no validation.
4. Block N proves successfully; the corrupted state is committed to L1.

**Block N+1 (impact):**

5. Any transaction that calls contract `C` triggers `execute_entry_point`.
6. `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` → returns `0` (undeclared).
7. `find_element(..., key=0)` → Cairo assertion failure; OS execution aborts.
8. Block N+1 cannot produce a valid proof.
9. All assets stored in contract `C` are permanently frozen; repeated inclusion of calls to `C` halts block finalization.