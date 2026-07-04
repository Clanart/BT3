### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract is a previously declared class before committing the state update. This is a direct analog to the Notional/Compound V2 finding: a required prerequisite step (approval / declaration check) is skipped before a critical state-mutating operation. A contract can set its own class hash to any arbitrary undeclared value, permanently rendering itself uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without consulting `contract_class_changes` (the dictionary that tracks declared classes) to confirm the hash is known to the OS:

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

The developer-inserted TODO comment explicitly acknowledges the missing check. [1](#0-0) 

Compare this with `execute_declare_transaction`, where a class is only committed to `contract_class_changes` after its hash is cryptographically verified against the Sierra component hashes:

```cairo
let expected_class_hash = finalize_class_hash(
    contract_class_component_hashes=contract_class_component_hashes
);
with_attr error_message("Invalid class hash pre-image.") {
    assert [class_hash_ptr] = expected_class_hash;
}
``` [2](#0-1) 

And the class is only written to `contract_class_changes` after that verification passes: [3](#0-2) 

`execute_replace_class` performs no equivalent lookup into `contract_class_changes` and no hash pre-image verification. The state update is unconditional once gas is sufficient.

---

### Impact Explanation

After `replace_class` commits an undeclared class hash to `contract_state_changes`, every subsequent transaction that targets the affected contract requires the prover to supply a compiled class (CASM) whose hash matches the new class hash. Because the hash was never declared, no valid compiled class exists in the global state or in `contract_class_changes`. The prover cannot construct a valid proof for any call to that contract. The contract is permanently uncallable.

Any ERC-20 tokens, ETH, or other assets stored in the contract's storage are permanently frozen — they cannot be transferred, withdrawn, or recovered.

**Impact category: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The `replace_class` syscall is available to every Cairo 1 contract without restriction. The attacker-controlled path is:

1. Deploy (or exploit) any contract that invokes the `replace_class` syscall.
2. Supply an arbitrary felt value as the new class hash — one that has never been declared on-chain.
3. The OS processes the syscall, updates `contract_state_changes`, and the block is proven.
4. The contract's on-chain class hash is now an undeclared value; it is permanently uncallable.

No privileged role, leaked key, or external dependency is required. Any unprivileged contract deployer can trigger this path. The risk is elevated for shared contracts (multisigs, DeFi vaults, account contracts) where an attacker can influence execution flow to reach a `replace_class` call.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, verify that the hash exists in `contract_class_changes` (declared in the current block) or in the committed global class trie (declared in a prior block). Concretely, add a lookup analogous to the one already performed in `execute_declare_transaction`:

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // or check global state trie
```

This mirrors the pattern used in `execute_declare_transaction` where `prev_value=0` enforces that a class may only be declared once and that the hash is valid before it is committed. [4](#0-3) 

---

### Proof of Concept

1. Attacker deploys a Cairo 1 contract `VictimVault` that holds user funds and exposes an `upgrade(new_class_hash: felt)` entry point that calls `replace_class(new_class_hash)`.
2. Attacker calls `upgrade(0xdeadbeef)` — an arbitrary felt that has never been declared.
3. The OS executes `execute_replace_class`:
   - Gas is deducted. [5](#0-4) 
   - `class_hash = 0xdeadbeef` is read from the request.
   - The TODO check is absent; no lookup into `contract_class_changes` occurs. [1](#0-0) 
   - `contract_state_changes` is updated: `VictimVault.class_hash = 0xdeadbeef`.
4. The block is proven and finalized. `VictimVault`'s on-chain class hash is now `0xdeadbeef`.
5. Any subsequent transaction targeting `VictimVault` requires a compiled class for `0xdeadbeef`. No such class exists. The prover cannot produce a valid proof. `VictimVault` is permanently uncallable.
6. All funds in `VictimVault`'s storage are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L886-894)
```text

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
