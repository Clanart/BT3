### Title
Unvalidated Class Hash in `execute_replace_class` Allows Permanent Contract Bricking and Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

Both the new and deprecated `execute_replace_class` syscall handlers in the StarkNet OS accept any arbitrary felt value as a new class hash without verifying that the hash corresponds to a declared contract class. This is directly analogous to the FujiOracle pattern: external input (the class hash from the syscall request) is consumed and committed to state without on-chain validity checks. A contract that replaces its class with an undeclared hash becomes permanently unexecutable, freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `request.class_hash` and immediately writes it into `contract_state_changes` with no on-chain assertion that the hash exists in `contract_class_changes`:

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

The TODO comment is the codebase's own acknowledgment that this check is missing. The same pattern is present in the deprecated path:

```cairo
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
    ...
``` [2](#0-1) 

The deprecated version has no TODO comment at all — the check is simply absent.

By contrast, the `execute_declare_transaction` path correctly enforces that a declared class hash is cryptographically derived from the Sierra class components before writing it to `contract_class_changes`:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
``` [3](#0-2) 

And class declarations enforce `prev_value=0` to prevent re-declaration:

```cairo
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [4](#0-3) 

`replace_class` performs no equivalent lookup into `contract_class_changes` to confirm the target hash was ever declared. The OS proof is still valid after the state transition, because the Cairo constraints only enforce that the dict update is self-consistent — not that the new class hash is reachable.

---

### Impact Explanation

Once a contract's `class_hash` field in `contract_state_changes` is set to an undeclared felt value, every subsequent transaction that attempts to call that contract will fail at class-loading time. The state change is committed on-chain and is irreversible (no `replace_class` back is possible if the contract cannot execute). Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen.

This matches: **Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The attack surface is any upgradeable contract pattern where the new class hash is supplied by an external caller (owner, governance vote, user-facing upgrade function). An attacker who can influence that argument — e.g., by winning a governance vote, front-running an upgrade call, or exploiting a missing access-control check in the upgrading contract — can supply an arbitrary felt that has never been declared. The OS will accept the proof, the L1 verifier will accept the proof, and the contract is bricked. The pattern of user-controlled upgrade targets is common in DeFi and account-abstraction contracts on StarkNet.

---

### Recommendation

Inside `execute_replace_class` (both the new and deprecated variants), add an on-chain assertion that the requested `class_hash` exists in `contract_class_changes` with a non-zero compiled class hash before committing the state update. Concretely:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // reverts if class is undeclared
```

This mirrors the invariant already enforced during `execute_declare_transaction` and closes the gap between declaration and replacement.

---

### Proof of Concept

1. Deploy contract `Vault` holding user funds; its class implements `replace_class` callable by an owner role.
2. Attacker gains owner access (or exploits a missing check) and calls `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` has never been declared.
3. The OS executes `execute_replace_class`: gas is deducted, `contract_state_changes[Vault_address].class_hash` is set to `0xdeadbeef`, the revert log records the old hash, and the function returns successfully.
4. The sequencer produces a valid STARK proof; the L1 verifier accepts it; the state root is updated.
5. Any subsequent `call_contract` or `invoke` targeting `Vault` causes the OS to look up `0xdeadbeef` in the class trie — it is absent — and the transaction fails.
6. All funds in `Vault`'s storage are permanently inaccessible. [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L817-819)
```text
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
