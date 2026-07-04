### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The OS-level `execute_replace_class` function (both the new and deprecated variants) does not verify that the supplied `class_hash` corresponds to a previously declared contract class before writing it into the contract state. A contract can therefore call `replace_class` with any arbitrary felt value — including `0` or a random undeclared hash — and the OS will accept the state transition. Once the contract's class hash is set to an undeclared value, every subsequent call to that contract will fail at class-lookup time, permanently freezing all assets stored in it.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash from the syscall buffer and immediately writes it into `contract_state_changes` with no check that the hash exists in `contract_class_changes`:

```cairo
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

The developer-left TODO at line 898 explicitly acknowledges the missing guard. The identical omission exists in the deprecated path (`deprecated_execute_syscalls.cairo`, `execute_replace_class`, lines 307–329), which also writes the caller-supplied hash directly into state without consulting `contract_class_changes`.

The OS is the authoritative source of truth for the STARK proof. If the OS accepts a state transition that sets a contract's class hash to an undeclared value, the resulting proof is valid from the L1 verifier's perspective, making the damage irreversible on-chain.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value:
- Every entry-point dispatch for that contract address will fail at class-lookup time (no compiled class exists for the hash).
- The contract's storage — including any ERC-20 balances, NFT ownership records, or protocol reserves — becomes permanently inaccessible.
- Because the OS proof is accepted by L1, there is no rollback path.

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any contract from within its own execution context. Realistic triggering scenarios include:

1. **Buggy upgrade logic** — a contract's upgrade function computes or receives the new class hash incorrectly (e.g., off-by-one in calldata parsing) and passes an undeclared hash. The OS provides no safety net.
2. **Malicious contract** — an attacker deploys a contract that deliberately calls `replace_class(0)` or `replace_class(<random felt>)`, then lures users to deposit funds before triggering the freeze.
3. **Reentrancy / cross-contract exploit** — an attacker manipulates a legitimate upgradeable contract into calling `replace_class` with an attacker-controlled hash during a callback.

No privileged role is required; any unprivileged transaction sender can deploy and invoke such a contract.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that it exists as a key in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled-class hash is non-zero. This mirrors the check that `execute_declare_transaction` enforces via `assert_not_zero(compiled_class_hash)` before updating `contract_class_changes`.

Apply the fix to both:
- `execute_replace_class` in `syscall_impls.cairo` (new syscall path)
- `execute_replace_class` in `deprecated_execute_syscalls.cairo` (deprecated syscall path)

---

### Proof of Concept

1. Declare class `A` (valid Sierra class) and deploy a contract `C` using it.
2. Deposit funds into `C` (e.g., ERC-20 transfer to `C`'s address).
3. Submit an invoke transaction that calls `C.__execute__`, which internally calls `replace_class(0x1337dead)` — a hash that has never been declared.
4. The OS executes `execute_replace_class`: gas is deducted, the TODO guard is absent, and `contract_state_changes[C].class_hash` is set to `0x1337dead`.
5. The block is proven and accepted on L1.
6. Any subsequent call to `C` fails: the OS cannot find a compiled class for `0x1337dead`, so every entry point reverts.
7. All funds deposited in step 2 are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2)

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
