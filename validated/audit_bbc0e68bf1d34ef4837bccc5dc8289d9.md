### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts an arbitrary class hash from the caller and writes it directly into the contract's state entry without verifying that the class hash corresponds to a previously declared contract class. This is structurally identical to the ThorSwap vault address bug: an unvalidated, attacker-supplied identifier is accepted and committed to protocol state. A contract holding user funds that calls `replace_class` with an undeclared class hash will have its class permanently set to an invalid value, making all future entry-point dispatches fail and permanently freezing any funds stored in that contract.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads the requested class hash directly from the syscall request struct and writes it into `contract_state_changes` with no check that the hash exists in `contract_class_changes`:

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

The developer-acknowledged TODO at line 898 explicitly confirms the missing guard. The OS proves and commits the new class hash into the state trie without ever consulting `contract_class_changes` to confirm the hash was declared. Once the block is proven and the state root is updated on L1, the invalid class hash is canonical and irrevocable.

Compare this with `execute_declare_transaction` in `transaction_impls.cairo` (lines 738–743), which enforces that a class hash must be the output of `finalize_class_hash` before it is written to `contract_class_changes`. No equivalent guard exists for `replace_class`.

Similarly, `deploy_contract` in `deploy_contract.cairo` (lines 44–49) validates the target address against a set of reserved addresses before writing state. `execute_replace_class` performs no analogous validation on the class hash.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After a successful `replace_class` call with an undeclared class hash:

1. The contract's `class_hash` field in the state trie is set to a value that has no corresponding entry in `contract_class_changes`.
2. Every subsequent call to the contract reaches the entry-point dispatch logic, which looks up the class hash to find entry points. Because the class is not declared, no entry points are found and every call reverts.
3. All ERC-20 balances, NFT ownership records, or any other assets stored in the contract's storage slots become permanently inaccessible — there is no upgrade path because `replace_class` itself is an entry point that can no longer be reached.
4. The state is proven and anchored on L1; there is no rollback mechanism.

---

### Likelihood Explanation

The entry path is reachable by any unprivileged transaction sender:

- A user deploys or interacts with any contract that exposes a `replace_class` call path (e.g., an upgradeable proxy, a DAO-governed vault, or any contract whose upgrade logic is callable by governance participants).
- The user supplies an arbitrary felt value as the new class hash — one that was never passed through `execute_declare_transaction`.
- The OS processes the syscall, writes the invalid hash to state, and includes it in the proven block output.
- No sequencer-level or OS-level check rejects the transaction; the hint `GetContractAddressStateEntry` only fetches the current state entry, it does not validate the new class hash.

The likelihood is elevated because upgradeable contract patterns are common on StarkNet, and the missing check is explicitly noted as a known gap in the codebase itself.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that it exists as a key in `contract_class_changes` (i.e., that it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned compiled class hash is non-zero, mirroring the pattern used in `execute_declare_transaction` where `assert_not_zero(compiled_class_hash)` is enforced before any state write.

---

### Proof of Concept

1. Declare class `A` (valid, goes through `execute_declare_transaction` — class hash `H_A` is written to `contract_class_changes`).
2. Deploy contract `C` using class `H_A`; fund it with 1000 STRK.
3. Class `A`'s `__execute__` calls `replace_class(class_hash=0xdeadbeef)` where `0xdeadbeef` is never declared.
4. Submit an invoke transaction targeting `C.__execute__`.
5. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef` — read at line 896.
   - The TODO guard is absent — line 898.
   - `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes` for address `C` — lines 906–910.
6. The block is proven; the state root on L1 now encodes `C.class_hash = 0xdeadbeef`.
7. Any subsequent call to `C` (including attempts to recover funds via another `replace_class`) fails at entry-point dispatch because `0xdeadbeef` has no declared class body.
8. The 1000 STRK stored in `C`'s storage are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-49)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
```
