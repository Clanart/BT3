### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not verify that the new class hash provided to the `replace_class` syscall corresponds to a previously declared contract class. This is the direct StarkNet analog of using `_mint` instead of `_safeMint`: just as `_mint` skips the recipient capability check and can freeze tokens in an incompatible contract, `execute_replace_class` skips the class existence check and can freeze all funds in a contract whose class hash is set to an undeclared value. The missing check is explicitly acknowledged by a TODO comment in the production code.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function (lines 877–916) accepts any arbitrary felt value as the new class hash and writes it directly into the contract's `StateEntry` without verifying that the hash corresponds to a class that has been declared in `contract_class_changes` or in the existing state:

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
```

The TODO comment at line 898 is a self-admission that the safety check is absent. The OS Cairo program is the authoritative constraint layer: if it does not constrain `class_hash` to be a declared class, a valid STARK proof can be generated for a state transition in which a contract's class hash is set to an arbitrary, undeclared value. The verifier on L1 will accept this proof, permanently committing the invalid class hash to the canonical state.

The same missing check exists in the deprecated syscall path in `deprecated_execute_syscalls.cairo` (lines 307–329), affecting Cairo 0 contracts as well. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every subsequent call to that contract will fail at the class-lookup stage because the OS cannot find the class bytecode to execute. There is no recovery path: the contract address is permanently associated with a non-existent class, and any ERC-20 tokens, ETH, or other assets held in the contract's storage become irretrievable. This is structurally identical to the `_mint` scenario in the external report, where an asset vault token is minted to a contract that cannot handle it, locking the vault and all its contents.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract from within its own execution. Any contract that exposes a function allowing a caller-controlled class hash argument (e.g., an upgradeable proxy pattern where the new implementation hash is passed as calldata) can be exploited by an unprivileged transaction sender. Additionally, a user who accidentally passes an undeclared class hash (e.g., a typo or off-by-one in a hash) will permanently destroy their own contract with no warning. Because the OS does not constrain the value, a valid proof is generated regardless, and the L1 verifier accepts it.

---

### Recommendation

In `execute_replace_class` (both in `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add a constraint that verifies the new `class_hash` exists in `contract_class_changes` (declared in the current block) or in the pre-existing committed class state before writing the new `StateEntry`. This mirrors the role of `_safeMint`'s `_checkOnERC721Received`: it is a mandatory safety gate that must be enforced at the protocol level, not left as an optional TODO.

---

### Proof of Concept

1. Alice deploys a vault contract (Cairo 1) that holds 1,000 STRK tokens and exposes an `upgrade(new_class_hash: felt252)` function that calls the `replace_class` syscall with the caller-supplied hash.
2. An attacker (or Alice herself, by mistake) calls `upgrade(0xdeadbeef)`, passing a felt value that has never been declared via a `declare` transaction.
3. The OS `execute_replace_class` function reads `request.class_hash = 0xdeadbeef`, skips the missing declared-class check (per the TODO at line 898), and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
4. The STARK proof is generated and verified on L1. The canonical state now records the vault contract's class hash as `0xdeadbeef`.
5. Any subsequent `call_contract` or `invoke` targeting the vault address causes the OS to look up class `0xdeadbeef` in `contract_class_changes` — it is absent. Execution fails unconditionally.
6. The 1,000 STRK tokens stored in the vault's storage are permanently frozen with no recovery mechanism. [3](#0-2)

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
