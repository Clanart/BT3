### Title
`replace_class` Syscall Accepts Undeclared Class Hashes, Enabling Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function does not verify that the new class hash supplied to the `replace_class` syscall corresponds to a previously declared contract class. Any contract can therefore replace its own class hash with an arbitrary, undeclared value. Once this happens, every subsequent call to that contract fails at the OS level because no compiled class exists for the new hash, permanently freezing any funds the contract holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads the requested class hash directly from the syscall request and writes it into the contract's `StateEntry` without any existence check:

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

The TODO comment at line 898 explicitly acknowledges the missing guard. Contrast this with `execute_declare_transaction`, which enforces `prev_value=0` to guarantee a class is declared at most once:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

Because `replace_class` never cross-checks the new hash against `contract_class_changes`, a caller can supply any felt value. After the state update is committed, the contract's `StateEntry.class_hash` points to a hash that has no entry in the compiled-class facts bundle. Every future invocation of the contract will fail when the OS attempts to resolve the class, making the contract permanently inoperable.

This is the direct structural analog of the `removeWrapping` bug: in that case, a mapping entry (`unwrapped[wrappedToken]`) was zeroed while dependent tokens still circulated, breaking all future unwrap calls. Here, the class-hash mapping for a live contract is overwritten with an invalid value while the contract still holds funds, breaking all future calls.

---

### Impact Explanation

After `replace_class` is called with an undeclared hash, the contract's `StateEntry.class_hash` is committed to the Patricia tree with a value that has no corresponding compiled class. The OS cannot execute any entry point on the contract. All assets (tokens, NFTs, protocol state) held by the contract are permanently inaccessible.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

- The missing check is explicitly flagged in the production source with a TODO dated 2026.
- The `replace_class` syscall is callable by any contract on itself; no privileged role is required.
- Upgradeable contracts, multisigs, governance contracts, and DeFi vaults commonly expose upgrade paths that accept a caller-supplied class hash. A single malicious or erroneous transaction through such a path is sufficient.
- No leaked keys, operator collusion, or network-level attack is needed — a standard `invoke` transaction suffices.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was previously registered by a `declare` transaction). The check should mirror the `prev_value=0` enforcement already present in `execute_declare_transaction`: read the current value for `class_hash` from `contract_class_changes` and assert it is non-zero (declared). If the hash is absent, write a failure response and return without mutating state.

---

### Proof of Concept

1. Deploy a contract `Vault` that holds user funds and exposes an `upgrade(new_class_hash)` function that calls the `replace_class` syscall with the supplied argument.
2. Submit an `invoke` transaction calling `Vault.upgrade(0xdeadbeef)`, where `0xdeadbeef` was never declared.
3. `execute_replace_class` writes `class_hash=0xdeadbeef` into `Vault`'s `StateEntry` with no validation. [3](#0-2) 
4. The block is proven and the new state root is committed. `Vault`'s class hash in the Patricia tree is now `0xdeadbeef`.
5. Any subsequent `invoke` targeting `Vault` causes the OS to look up compiled class facts for `0xdeadbeef`, find nothing, and fail.
6. All funds inside `Vault` are permanently frozen; no withdrawal, transfer, or recovery function can execute.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
