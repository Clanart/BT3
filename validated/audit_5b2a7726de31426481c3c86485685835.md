### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program accepts an arbitrary, user-controlled class hash from the `replace_class` syscall request and writes it directly into the contract state without verifying that the class hash was ever declared. This is the direct analog of the reported IP address validation bug: user-supplied input flows into a security-critical state update with no validation gate. A contract can permanently brick itself (and freeze all funds it holds) by replacing its class hash with an undeclared, non-existent value.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `request.class_hash` from the caller-controlled syscall segment and immediately uses it to overwrite the contract's `StateEntry.class_hash` in `contract_state_changes`:

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

The TODO comment at line 898 is an explicit, in-code acknowledgment that the required validation — checking whether `class_hash` exists in `contract_class_changes` (i.e., was previously declared) — is **absent**. [2](#0-1) 

The `contract_class_changes` dictionary maps `class_hash → compiled_class_hash` and is populated only by `declare` transactions. [3](#0-2) 

The `replace_class` syscall is dispatched from `execute_syscalls` without any pre-check on the class hash value. [4](#0-3) 

The class hash written into state is taken verbatim from the syscall request struct, which is populated from contract-controlled memory — making it fully attacker-controlled. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's `class_hash` in `contract_state_changes` is set to a value that has no entry in `contract_class_changes`, every subsequent call to that contract will fail at class resolution: the OS cannot find a compiled class for the hash, so no entry point can be dispatched. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. Because the OS is the authoritative state-transition verifier whose output is committed on L1, this state is irreversible — no future transaction can recover the funds.

---

### Likelihood Explanation

The attack surface is wide. Any deployed contract can issue the `replace_class` syscall with an arbitrary felt value as the class hash. No privilege, special role, or leaked key is required — only the ability to deploy and call a contract. The missing check is explicitly flagged in the source with a TODO, confirming it is a known gap in the current OS implementation. A malicious sequencer can include such a transaction and generate a valid OS proof because the OS itself does not enforce the invariant.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, verify that the provided hash exists in `contract_class_changes` (i.e., its compiled class hash is non-zero). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `request.class_hash` and assert the result is non-zero before proceeding with the state update. This mirrors how `execute_declare_transaction` enforces `assert_not_zero(compiled_class_hash)` before writing to `contract_class_changes`. [6](#0-5) 

---

### Proof of Concept

1. Deploy contract `Victim` that holds user funds (e.g., acts as a simple vault).
2. `Victim`'s code calls the `replace_class` syscall with `class_hash = 0xdeadbeef` — a felt value that was never passed through a `declare` transaction and therefore has no entry in `contract_class_changes`.
3. `execute_replace_class` in the OS reads `request.class_hash = 0xdeadbeef` and writes it directly into `Victim`'s `StateEntry` via `dict_update` on `contract_state_changes`. No validation occurs. [1](#0-0) 
4. The OS produces a valid proof for this state transition; L1 accepts it.
5. Any subsequent call to `Victim` fails at class resolution — the OS cannot find a compiled class for `0xdeadbeef`.
6. All funds stored in `Victim`'s storage are permanently frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L885-913)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L269-275)
```text
    %{ InitializeClassHashes %}
    // A dictionary from class hash to compiled class hash (Casm).
    let (contract_class_changes: DictAccess*) = dict_new();

    return (
        contract_state_changes=contract_state_changes, contract_class_changes=contract_class_changes
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
