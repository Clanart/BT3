### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The OS-level `execute_replace_class` handler accepts any arbitrary felt value as the new class hash without verifying that the hash corresponds to a previously declared contract class. This is the direct analog of the EsEMBR `addVester()` overwrite bug: in both cases a registry entry is mutated without validating the new value against existing on-chain state, leaving the affected slot in a permanently broken condition. A contract whose class hash is replaced with an undeclared value becomes permanently uncallable, freezing all funds it holds.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads `class_hash` directly from the raw syscall request and writes it into `contract_state_changes` with no cross-check against `contract_class_changes`:

```cairo
let class_hash = request.class_hash;          // attacker-supplied felt

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

The developer-left TODO at line 898 explicitly acknowledges the missing invariant. The identical gap exists in the deprecated path: [2](#0-1) 

By contrast, the `execute_declare_transaction` path correctly enforces `prev_value=0` to prevent re-declaration of an existing class hash, demonstrating that the OS *does* know how to guard registry writes — the guard is simply absent in `execute_replace_class`: [3](#0-2) 

`StateEntry.class_hash` is the field being overwritten: [4](#0-3) 

---

### Impact Explanation

Once the OS commits a block in which a contract's `class_hash` is set to an undeclared value, every subsequent transaction that targets that contract will fail at class-lookup time — there is no declared bytecode to dispatch to. Because the state root is already committed, the replacement cannot be undone. Any ERC-20 balance, LP position, or other asset held inside the contract is permanently inaccessible.

**Impact class**: Critical — Permanent freezing of funds.

---

### Likelihood Explanation

The `replace_class` syscall is a standard StarkNet syscall reachable by any Sierra contract. Two realistic paths exist:

1. **Attacker-controlled contract**: A user deploys a contract whose logic calls `replace_class` with a crafted, undeclared hash. Any funds deposited into that contract by other users (e.g., a shared vault, a token bridge endpoint) are permanently frozen.
2. **Reentrancy / callback abuse in an existing contract**: A contract that invokes an external callback before completing its own state update can be manipulated into calling `replace_class` with attacker-supplied data during the callback, replacing the victim contract's class with an invalid hash.

Both paths require only an unprivileged transaction sender; no operator key or privileged role is needed.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, assert that the hash is present in `contract_class_changes` with a non-zero compiled-class value. Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned value is non-zero, mirroring the `prev_value=0` guard already used in `execute_declare_transaction`. This closes the gap acknowledged by the existing TODO comment.

---

### Proof of Concept

1. Attacker deploys contract `V` (a shared vault) that accepts deposits from arbitrary users and exposes a `replace_class(new_hash: felt)` external entry point that directly calls the `replace_class` syscall with the caller-supplied value.
2. Users deposit funds into `V`; `V` now holds significant assets.
3. Attacker calls `V.replace_class(0xdeadbeef)`. The OS executes `execute_replace_class`, reads `class_hash = 0xdeadbeef` from the syscall request, and writes it into `contract_state_changes` for `V`'s address — **no validation is performed** (line 898 TODO).
4. The block is proven and the new state root is committed on L1. `V`'s `class_hash` is now `0xdeadbeef`, which has no entry in `contract_class_changes`.
5. Any subsequent transaction targeting `V` (withdraw, transfer, etc.) fails at class dispatch because `0xdeadbeef` is not a declared class. All deposited funds are permanently frozen with no recovery path.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```
