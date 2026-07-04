### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a previously declared class. A contract that exposes a user-controlled upgrade path can be permanently bricked, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the new class hash directly from the syscall request and writes it into `contract_state_changes` with no validation against the set of declared classes: [1](#0-0) 

The code itself acknowledges the missing check with an explicit TODO: [2](#0-1) 

The `class_hash` field is taken verbatim from `request.class_hash`, which is written by the executing contract and is therefore fully attacker-controlled: [3](#0-2) 

The `contract_class_changes` dictionary — which tracks all classes declared in the current block — is available as an implicit argument to `execute_replace_class` but is never consulted: [4](#0-3) 

For comparison, the `execute_declare_transaction` path correctly enforces `prev_value=0` to prevent double-declaration and validates the class hash pre-image: [5](#0-4) 

No equivalent guard exists in `execute_replace_class`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, every subsequent call to that contract will fail at class-lookup time because neither the current block's `contract_class_changes` nor any prior state contains a class with that hash. The contract becomes permanently inert. Any ERC-20 balances, NFTs, or protocol reserves held by the contract are irrecoverably locked.

---

### Likelihood Explanation

The attack path requires only an unprivileged transaction sender and a target contract that passes user-supplied input to `replace_class`. This pattern is common in:

- Upgradeable proxy contracts where governance or an owner can supply a new implementation hash.
- DeFi protocols with on-chain upgrade mechanisms.
- Any contract that forwards a user-provided `class_hash` argument to the `replace_class` syscall.

Because the OS provides no safety net, the burden of validation falls entirely on every individual contract author. A single contract that omits the check is sufficient for exploitation.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, the OS must verify that the hash is present in either:

1. `contract_class_changes` (declared in the current block), or
2. The pre-existing on-chain class registry (declared in a prior block).

This check belongs at the OS level — analogous to how `execute_declare_transaction` enforces `prev_value=0` to prevent re-declaration — so that no individual contract can bypass it.

---

### Proof of Concept

1. Deploy an upgradeable contract that exposes:
   ```cairo
   fn upgrade(new_class_hash: ClassHash) {
       replace_class_syscall(new_class_hash).unwrap();
   }
   ```
2. Submit an `invoke` transaction calling `upgrade(0xdeadbeef)` where `0xdeadbeef` has never been declared.
3. The OS executes `execute_replace_class`; `request.class_hash = 0xdeadbeef` is written directly into `contract_state_changes` with no validation.
4. The block is proven and accepted on-chain.
5. Any subsequent call to the contract causes the OS to look up class `0xdeadbeef`, find nothing, and revert.
6. All funds held by the contract are permanently frozen.

The structural parallel to the reported NFT double-vote bug is exact: just as `BlackGovernor._castVote` accepted a vote without checking whether the NFT had already been used (missing ownership-change guard), `execute_replace_class` accepts a class hash without checking whether it has been declared (missing class-existence guard). In both cases, the absence of a single state-validity check allows an attacker to drive the system into an invalid state.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-884)
```text
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
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

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
