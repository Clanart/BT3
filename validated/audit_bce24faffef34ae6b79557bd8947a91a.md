### Title
Missing Class Declaration Validation in `execute_replace_class` Enables Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the replacement class hash is actually declared on-chain. A contract can replace its own class with an arbitrary, undeclared class hash. After this state transition is committed, any subsequent call into that contract will permanently fail at the OS level because the compiled class cannot be resolved. Any funds held by the contract are permanently frozen.

---

### Finding Description

**Phase 1 — Unchecked `replace_class` (the bypassed validation):**

`execute_replace_class` in `syscall_impls.cairo` (lines 877–916) processes the syscall and unconditionally writes the caller-supplied `class_hash` into `contract_state_changes` with no check that the hash corresponds to a declared class:

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

The `TODO` comment at line 898 explicitly acknowledges the missing check. The OS proof for the block containing this syscall is generated successfully — the state transition is accepted.

**Phase 2 — Enforced class lookup on every subsequent call (the irrecoverable failure):**

Every subsequent call into the contract goes through `execute_entry_point` in `execute_entry_point.cairo`. The OS first resolves the compiled class hash from `contract_class_changes`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
```

Because the replacement class hash was never declared, `dict_read` returns the default value `0`. The OS then calls `find_element` to locate the compiled class:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

`find_element` uses a hint to locate the element; if `compiled_class_hash = 0` is absent from `compiled_class_facts_bundle`, the hint fails and the OS proof cannot be generated for any block that includes a successful call to this contract. The sequencer is therefore forced to mark every call to the contract as reverted, making the contract permanently uncallable and any funds it holds permanently frozen.

The two-phase inconsistency mirrors the original report exactly:

| | Original (ERC1155) | StarkNet OS Analog |
|---|---|---|
| Phase 1 | Mint during constructor — no receiver check (no code yet) | `replace_class` — no declaration check (check missing) |
| Phase 2 | Mint during withdrawal — receiver check enforced (code exists) | Call contract — class lookup enforced (`find_element`) |
| Result | Funds stuck in staking contract | Funds stuck in replaced contract |

---

### Impact Explanation

Any ERC-20 or native token balance held by a contract that has undergone an undeclared `replace_class` is permanently inaccessible. The sequencer cannot include a successful call to the contract in any provable block. This satisfies the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer:

1. Deploy a vault/pool contract that accepts user token deposits.
2. After users deposit, trigger a call to `replace_class` (either via a backdoor entry point or a time-locked mechanism) supplying an arbitrary undeclared felt as the new class hash.
3. The OS accepts the state transition (no check).
4. All subsequent withdrawal calls revert permanently.

No privileged role, leaked key, or external dependency is required. The root cause is entirely within the in-scope OS Cairo code.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., has been declared). The existing TODO at line 898 already identifies this gap:

```cairo
// Enforce: the replacement class must be declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the two phases consistent.

---

### Proof of Concept

1. Declare class `C` and deploy vault contract `V` using `C`. `V` exposes `deposit()` and `withdraw()`, and contains a hidden `backdoor()` entry point that calls `replace_class(undeclared_hash)`.
2. Users call `deposit()` on `V`, transferring tokens into `V`'s storage.
3. Attacker calls `backdoor()`. The OS executes `execute_replace_class` with `class_hash = undeclared_hash`. No declaration check is performed (line 898 TODO). `contract_state_changes[V].class_hash` is updated to `undeclared_hash`. The block is proven successfully.
4. A user submits a `withdraw()` call to `V`. The sequencer executes it: `execute_entry_point` reads `contract_class_changes[undeclared_hash]` → returns `0`; `find_element(..., key=0)` fails → sequencer marks the transaction reverted.
5. The OS proves the reverted transaction (validate step on the user's account contract only; `V` is never entered). Proof succeeds. Funds remain in `V`'s storage forever.

**Relevant code locations:** [1](#0-0) 

<cite repo="Annirich/sequencer--001" path="crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution

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
