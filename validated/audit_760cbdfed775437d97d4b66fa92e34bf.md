### Title
Missing Declared Class Validation in `replace_class` Syscall Allows Permanent Freezing of Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied by a contract actually corresponds to a declared class in the on-chain class tree. This allows any unprivileged contract caller to replace a contract's class with an arbitrary, undeclared class hash. The OS will accept and commit this invalid state transition, permanently freezing any funds held by the affected contract.

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the class hash exists in `contract_class_changes` (the declared class tree):

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
``` [1](#0-0) 

The developer-acknowledged TODO at line 898 explicitly states the missing check. The `contract_class_changes` dict (which maps `class_hash → compiled_class_hash`) is never consulted. The OS then commits this state — including the invalid class hash — into the Patricia Merkle Tree via `state_update` and `compute_contract_state_commitment`: [2](#0-1) 

The post-execution validation (`validate_compiled_class_facts_post_execution`) only validates class facts that were *executed* during the block, not class hashes stored in contract state entries via `replace_class`: [3](#0-2) 

This is structurally analogous to the external report's `DiamondCutFacet` finding: just as that vulnerability allows adding unaudited/non-existent code as a trusted facet, this vulnerability allows any contract to register an arbitrary, non-existent class hash as its implementation — with no protocol-level rejection.

### Impact Explanation

Once a contract's class hash is replaced with an undeclared value and the block is proven and committed to L1:

1. The contract's `StateEntry.class_hash` in the global state tree points to a class that does not exist in the class tree.
2. Any subsequent transaction attempting to call or invoke the contract will fail at class lookup, as the class bytecode cannot be found.
3. Any ERC-20 tokens, ETH, or STRK held in the contract's storage are permanently inaccessible — no withdrawal, transfer, or recovery is possible.

**Impact: Critical — Permanent freezing of funds.**

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly accessible syscall callable by any contract during execution. No privileged role is required. An attacker needs only to:

- Deploy a contract (or exploit an existing one with a `replace_class` call path), and
- Supply an arbitrary felt value as the new class hash.

The gas cost of `REPLACE_CLASS_GAS_COST = 10670` is low, making this cheap to execute. The attack is deterministic and requires no special conditions beyond controlling a contract that calls `replace_class`. [4](#0-3) 

### Recommendation

Before writing the new class hash into `contract_state_changes`, the OS must verify that the class hash exists in `contract_class_changes`. Specifically, `execute_replace_class` should perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert that the returned compiled class hash is non-zero (i.e., the class has been declared). This is exactly what the existing TODO comment acknowledges must be done.

### Proof of Concept

1. Attacker deploys `VaultContract` which accepts ETH deposits and exposes a `replace_class(new_hash)` entry point that calls the `replace_class` syscall.
2. Users deposit funds into `VaultContract`.
3. Attacker calls `VaultContract.replace_class(0xdeadbeef)` — an arbitrary felt that is not a declared class hash.
4. The OS executes `execute_replace_class`, writes `class_hash=0xdeadbeef` into `contract_state_changes` for `VaultContract`'s address, with no validation against `contract_class_changes`.
5. `state_update` commits this into the Patricia tree; the block is proven and posted to L1.
6. All subsequent calls to `VaultContract` fail — the class `0xdeadbeef` does not exist. All deposited funds are permanently frozen. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L69-87)
```text
    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L114-120)
```text
    // Validate the guessed compile class facts.
    let compiled_class_facts_bundle = os_global_context.compiled_class_facts_bundle;
    validate_compiled_class_facts_post_execution(
        n_compiled_class_facts=compiled_class_facts_bundle.n_compiled_class_facts,
        compiled_class_facts=compiled_class_facts_bundle.compiled_class_facts,
        builtin_costs=compiled_class_facts_bundle.builtin_costs,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L111-111)
```text
const REPLACE_CLASS_GAS_COST = 10670;
```
