### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts and commits a new class hash to the global state without verifying that the class hash has been declared. An unprivileged contract caller can supply an undeclared (non-existent) class hash, causing the OS to permanently write an invalid class hash for the target contract. All subsequent calls to that contract will fail at class-lookup time, permanently freezing any funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any validation:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
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

The developer-acknowledged TODO at line 898 confirms the missing check. The OS unconditionally commits `class_hash` — which may be any arbitrary felt value — into the contract state. This state is then squashed and committed to the global Patricia trie via `state_update` in `state.cairo`. Once committed, the contract's class hash permanently points to a non-existent compiled class.

The analog to the Lido bug is direct: just as the stETH Burner could burn WstETH's shares without checking whether WstETH would still be functional afterward (blocking `unwrap`), the StarkNet OS here accepts a `replace_class` call without checking whether the new class is actually usable, permanently blocking all future entry-point dispatches to that contract.

---

### Impact Explanation

**Critical — Permanent Freezing of Funds.**

Once a contract's class hash is set to an undeclared value and the block is proven, the state is final. Every subsequent transaction targeting that contract will fail at class resolution time (the OS cannot find a compiled class for the hash). Any ERC-20 tokens, ETH bridged via L1→L2 messages, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path because the state root is already committed on L1.

---

### Likelihood Explanation

**Medium.**

The direct trigger requires a contract to call `replace_class` with an attacker-controlled or invalid hash. This is reachable in several realistic scenarios:

1. **Attacker-deployed contract**: An unprivileged user deploys a contract whose constructor or any public entry point calls `replace_class(attacker_chosen_hash)`. If the contract holds funds (e.g., it is a shared escrow or vault), those funds are frozen.
2. **Upgradeable protocol with weak access control**: Any DeFi protocol that exposes an upgrade path via `replace_class` without off-chain or on-chain validation of the new class hash is vulnerable. An attacker who can invoke the upgrade function (e.g., through a governance exploit, a missing access-control check, or a reentrancy path) can supply an undeclared hash.
3. **L1→L2 message handler**: A contract that processes L1 handler messages and internally calls `replace_class` based on message payload is directly exploitable by any L1 message sender.

The OS is a necessary step in all cases: it is the only place where the class-hash validity check should be enforced, and it currently is not.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, verify that `class_hash` exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned compiled class hash is non-zero. This mirrors the existing pattern used in `execute_declare_transaction` where `assert_not_zero(compiled_class_hash)` is enforced before writing to `contract_class_changes`.

---

### Proof of Concept

1. Deploy contract `VaultWithUpgrade` that:
   - Holds user ERC-20 deposits in its storage.
   - Exposes a public `upgrade(new_class_hash: felt)` entry point that calls `replace_class(new_class_hash)`.

2. As an unprivileged user, invoke `upgrade(0xdeadbeef)` where `0xdeadbeef` is not declared on-chain.

3. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef` [1](#0-0) 
   - The TODO check is absent; no validation occurs. [2](#0-1) 
   - `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes`. [3](#0-2) 

4. `state_update` in `state.cairo` squashes and commits this entry to the global state root. [4](#0-3) 

5. The block is proven and the new state root (containing `VaultWithUpgrade.class_hash = 0xdeadbeef`) is posted to L1.

6. All subsequent calls to `VaultWithUpgrade` — including user withdrawal functions — fail permanently because the OS cannot resolve `0xdeadbeef` to any compiled class. All deposited funds are frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-896)
```text
    let class_hash = request.class_hash;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L898-898)
```text
    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L906-910)
```text
    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L58-74)
```text
    let (
        n_contract_state_changes, squashed_contract_state_changes_start
    ) = squash_state_changes_and_maybe_allocate_aliases(
        contract_state_changes_start=os_state_update.contract_state_changes_start,
        contract_state_changes_end=os_state_update.contract_state_changes_end,
        should_allocate_aliases=should_allocate_aliases,
    );

    // State is finalized.
    %{ ComputeCommitmentsOnFinalizedStateWithAliases %}

    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );
```
