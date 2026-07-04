### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Replacement with Undeclared or Zero Class Hash — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not validate that the new class hash supplied by a contract is (a) a declared class or (b) non-zero (`UNINITIALIZED_CLASS_HASH`). This is the direct analog of the reported Cosmos SDK bug: just as the initial consensus key was omitted from the `NewToOldConsKeyMap` allowing rotation back to the initial key, the initial/uninitialized class hash (`0`) is not guarded against in `execute_replace_class`, allowing a contract to replace its class with the uninitialized sentinel value and permanently freeze any funds it holds.

---

### Finding Description

In `execute_replace_class` the OS reads the requested class hash from the syscall request and immediately writes it into `contract_state_changes` with no validation:

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

Two validations are absent:

1. **No check that `class_hash` is declared** — explicitly acknowledged by the TODO comment dated 1/1/2026 (now overdue).
2. **No check that `class_hash != UNINITIALIZED_CLASS_HASH` (0)** — `UNINITIALIZED_CLASS_HASH` is the sentinel value used by `deploy_contract` to detect an undeployed slot. [2](#0-1) 

The analog to the original report is exact:

| Original (Cosmos SDK) | StarkNet OS analog |
|---|---|
| Initial consensus key never inserted into `NewToOldConsKeyMap` | `UNINITIALIZED_CLASS_HASH = 0` never guarded against in `execute_replace_class` |
| Validator can rotate **back** to the initial key | Contract can replace its class **back** to the uninitialized sentinel (0) |
| Compromised initial key can be reused | Contract becomes permanently unusable; funds frozen |

The `deploy_contract` function enforces `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` to prevent double-deployment, but it also checks `assert state_entry.nonce = 0`. Because a live contract will have a non-zero nonce, once its class hash is set to 0 via `replace_class(0)`, the contract **cannot be redeployed** — it is permanently bricked. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract calls `replace_class(0)` or `replace_class(<undeclared_hash>)`:

1. The OS updates `contract_state_changes` with the invalid class hash — no revert, no error.
2. The state commitment (`compute_contract_state_commitment`) hashes and commits this invalid class hash into the Patricia tree, producing a valid proof accepted by L1.
3. Every subsequent call to the contract fails: the OS cannot load a class for hash `0` or an undeclared hash.
4. The contract is permanently unusable. All ERC-20 tokens, ETH, or protocol-specific assets held by the contract are permanently frozen with no recovery path. [4](#0-3) 

---

### Likelihood Explanation

The root cause is confirmed in the OS Cairo code by the explicit TODO comment. The OS is the authoritative source of truth for proof validity. Two realistic paths exist:

**Path A (unprivileged user, no malicious sequencer):** If the blockifier's `replace_class` handler also omits this validation (likely, since the OS is the reference implementation and the TODO implies the check was never added anywhere), any contract deployer can call `replace_class(0)` from their own contract. The transaction succeeds, the OS produces a valid proof, and the contract is permanently bricked.

**Path B (malicious sequencer):** Even if the blockifier rejects such a call, a malicious sequencer can include the raw state transition. Because the OS does not enforce the invariant, the resulting proof is valid and L1 accepts it, permanently freezing the targeted contract's funds.

Path A is the higher-likelihood scenario given the TODO's age and scope.

---

### Recommendation

Inside `execute_replace_class`, before writing to `contract_state_changes`, add:

1. **Guard against `UNINITIALIZED_CLASS_HASH`:** Assert `class_hash != 0`.
2. **Guard against undeclared classes:** Read `contract_class_changes` (or a finalized class set) and assert that `class_hash` maps to a non-zero compiled class hash, resolving the existing TODO.

```cairo
// Proposed fix (pseudocode):
assert_not_zero(class_hash);  // Prevent replace_class(0)

// Resolve TODO: verify class_hash is declared
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // Ensure class is declared
```

---

### Proof of Concept

1. Attacker deploys `MaliciousContract` whose constructor or any callable function executes `replace_class(syscall_ptr, class_hash=0)`.
2. Attacker (or any user) calls the function. The OS processes `execute_replace_class`:
   - `request.class_hash = 0`
   - No validation fires (TODO not implemented)
   - `dict_update` writes `StateEntry(class_hash=0, ...)` for `MaliciousContract`'s address
3. `compute_contract_state_commitment` hashes and commits `class_hash=0` into the Patricia tree.
4. The proof is generated and accepted by L1.
5. Any subsequent `call_contract` or `invoke` targeting `MaliciousContract` fails: the OS reads `class_hash=0` from state, finds no compiled class, and the execution aborts.
6. All funds (tokens, ETH) held by `MaliciousContract` are permanently frozen. `deploy_contract` cannot redeploy to the same address because `state_entry.nonce != 0`. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L76-111)
```text
func compute_contract_state_commitment{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_state_changes_start: DictAccess*,
    n_contract_state_changes: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> CommitmentUpdate {
    alloc_locals;

    // Hash the entries of the contract state changes to prepare the input for the commitment tree
    // multi-update.
    let (local hashed_state_changes: DictAccess*) = alloc();
    compute_contract_state_commitment_inner(
        state_changes=contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        hashed_state_changes=hashed_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the contracts' state tree.
    local initial_root;
    local final_root;

    %{ SetPreimageForStateCommitments %}

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_state_changes,
        n_updates=n_contract_state_changes,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (CommitmentUpdate(initial_root=initial_root, final_root=final_root));
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-66)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```
