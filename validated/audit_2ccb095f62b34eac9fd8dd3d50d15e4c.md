### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared class. This is explicitly acknowledged by a `TODO` comment in the code. As a result, any contract can permanently replace its own class hash with an arbitrary undeclared value, rendering itself permanently non-executable and freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function accepts any arbitrary `class_hash` felt value from the syscall request and writes it directly into `contract_state_changes` without checking whether that hash exists in the `contract_class_changes` dictionary (i.e., whether it was ever declared):

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

The same missing check exists in the deprecated path: [2](#0-1) 

By contrast, the `execute_declare_transaction` function enforces that a class can only be declared once and that the class hash is a valid Sierra hash:

```cairo
// Note that prev_value=0 enforces that a class may be declared only once.
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [3](#0-2) 

The `deploy_contract` function also enforces that the target address is uninitialized before deployment: [4](#0-3) 

But `replace_class` has no equivalent guard. The OS protocol invariant — that every contract's `class_hash` field must correspond to a declared class — is not enforced at the point of class replacement.

The `replace_class` syscall is dispatched from both the new and deprecated syscall routers: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` is set to an undeclared value and the block is finalized, the state commitment is updated with this invalid class hash: [7](#0-6) 

Any subsequent call to the contract will fail at the entry-point dispatch stage because no compiled class exists for the stored hash. There is no recovery mechanism — the contract is permanently bricked. All ERC-20 balances, NFTs, or other assets held in the contract's storage are permanently inaccessible.

---

### Likelihood Explanation

**High.** The `replace_class` syscall is callable by any deployed contract during execution. An attacker can:

1. Deploy a contract that accepts user deposits (e.g., a token vault or escrow).
2. Attract user funds into the contract.
3. Call `replace_class` with an arbitrary undeclared felt (e.g., `1`).
4. The OS accepts the state transition without validation.
5. The contract is permanently non-executable; all deposited funds are frozen.

Additionally, a legitimate contract with a bug in its `replace_class` logic (e.g., accepting a user-supplied class hash without validation) can be exploited by an unprivileged transaction sender to trigger the same outcome. The OS is the last line of defense and currently provides none.

---

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). The check should mirror the `prev_value=0` enforcement used in `execute_declare_transaction`. Concretely, perform a `dict_read` on `contract_class_changes` for the given `class_hash` and assert the result is non-zero before proceeding with the `dict_update` on `contract_state_changes`. This check must be added to both `execute_replace_class` implementations (in `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`).

---

### Proof of Concept

1. Attacker deploys contract `VaultContract` with a valid declared class hash `C`.
2. Users deposit funds into `VaultContract`.
3. Attacker calls a function in `VaultContract` that internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
4. `execute_replace_class` in `syscall_impls.cairo` line 896–913 accepts the request, skipping the declared-class check (the TODO at line 898), and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
5. The block is finalized. `compute_contract_state_commitment` hashes the new state entry including `class_hash=0xdeadbeef` and updates the Patricia tree root.
6. Any subsequent transaction targeting `VaultContract` fails at entry-point dispatch — no compiled class for `0xdeadbeef` exists.
7. All user funds in `VaultContract` are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L676-688)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(
            contract_address=execution_context.execution_info.contract_address,
            syscall_ptr=cast(syscall_ptr, ReplaceClass*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - ReplaceClass.SIZE,
            syscall_ptr=syscall_ptr + ReplaceClass.SIZE,
        );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L196-203)
```text
    let (new_value) = get_contract_state_hash(
        class_hash=new_state.class_hash,
        storage_root=final_contract_state_root,
        nonce=new_state.nonce,
    );

    assert hashed_state_changes.new_value = new_value;
    assert hashed_state_changes.key = contract_address;
```
