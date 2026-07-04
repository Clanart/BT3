### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not verify that the caller-supplied `class_hash` corresponds to a previously declared contract class before committing the class replacement to state. This is directly analogous to the external report's "Missing Fund Check": a required state condition (`funded == true` / class is declared) is not enforced before a critical, irreversible state mutation. The missing check is explicitly acknowledged by a TODO comment in the source.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` processes the `replace_class` syscall as follows:

1. Reduces gas and writes the response header.
2. Reads `class_hash` from the request.
3. Fetches the current `StateEntry` for the calling contract.
4. Immediately writes a new `StateEntry` with the caller-supplied `class_hash` into `contract_state_changes`. [1](#0-0) 

The critical missing step is acknowledged by the developer comment at line 898:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [2](#0-1) 

There is no assertion, `dict_read`, or any other constraint verifying that `class_hash` exists in `contract_class_changes` (the declared-class registry) before the `dict_update` that permanently replaces the contract's class. [3](#0-2) 

By contrast, the `execute_declare_transaction` path in `transaction_impls.cairo` enforces `assert_not_zero(compiled_class_hash)` and uses `dict_update` with `prev_value=0` to guarantee a class is registered exactly once before it can be used. [4](#0-3) 

The `execute_replace_class` function performs no equivalent check.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a contract's class hash is replaced with a value that has no corresponding declared class, every subsequent call to that contract will fail at the OS level when the prover attempts to look up the class definition. Because the state mutation is committed to the Merkle tree and included in the proven block output, the contract's class hash is permanently set to the invalid value. Any funds held by that contract (ERC-20 balances, vault deposits, multi-sig assets) become permanently inaccessible — the contract cannot execute any entry point, including withdrawal or transfer functions.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any deployed contract without privilege. A contract author (or a contract with a logic bug) can supply an arbitrary felt as the new class hash. Because the OS — the authoritative Cairo program whose output is verified on L1 — performs no declared-class membership check, a sequencer that includes such a transaction produces a valid proof. The sequencer's off-chain blockifier may provide a secondary check, but the OS is the protocol's root of trust; the absence of this check at the OS layer means the invariant is not cryptographically enforced. The explicit TODO comment confirms this is a known, unimplemented guard.

---

### Recommendation

Before the `dict_update` in `execute_replace_class`, add a read from `contract_class_changes` to assert that the requested `class_hash` maps to a non-zero compiled class hash (i.e., it has been declared). This mirrors the pattern already used in `execute_declare_transaction`:

```cairo
// Assert the class is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This enforces at the proof level — not just the sequencer level — that `replace_class` can only target a previously declared class, closing the state-transition bypass.

---

### Proof of Concept

1. Attacker deploys contract `C` holding user funds.
2. `C` executes the `replace_class` syscall with `class_hash = 0xdeadbeef` (not declared).
3. The OS's `execute_replace_class` passes gas checks, fetches the current `StateEntry`, and calls `dict_update` setting `C`'s class hash to `0xdeadbeef` — no declared-class membership check occurs. [5](#0-4) 
4. The block is proven. The L1 verifier accepts the proof. The state root now encodes `C.class_hash = 0xdeadbeef`.
5. Any future transaction targeting `C` causes the OS to look up `0xdeadbeef` in the class registry, find nothing, and fail — making `C` permanently non-executable.
6. All funds deposited in `C` are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
