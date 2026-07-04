### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `execution/syscall_impls.cairo`)

### Summary
The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash provided by a contract corresponds to a previously declared contract class. This allows any contract to replace its own class hash with an arbitrary, undeclared value, permanently freezing the contract and any funds it holds.

### Finding Description
In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall by directly updating the contract's class hash in `contract_state_changes` without checking whether the new class hash is a declared class. The code even contains an explicit TODO acknowledging this missing check:

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
``` [1](#0-0) 

After the state is committed with the invalid class hash, any subsequent call to the contract enters `execute_entry_point`, which does:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

Since the undeclared class hash has no entry in `contract_class_changes`, `dict_read` returns 0. `find_element` then fails to locate a compiled class with hash 0, causing a Cairo assertion failure and making any proof that includes a call to this contract invalid. The contract is permanently uncallable.

### Impact Explanation
**Critical — Permanent freezing of funds.** Once a contract's class hash is replaced with an undeclared value and the state is committed on-chain, the contract becomes permanently frozen. No valid OS proof can ever include a successful call to that contract again. Any ERC-20 tokens, ETH, STRK, or other assets held in the contract's storage are irrecoverably locked.

### Likelihood Explanation
**Medium.** The `replace_class` syscall is available to any deployed contract — no privileged role is required. A malicious actor can:
- Deploy a contract that accepts deposits
- Attract user funds into it
- Trigger `replace_class` with an arbitrary undeclared class hash (e.g., `felt(0xdeadbeef)`)
- Permanently freeze all deposited funds

Additionally, a buggy contract could accidentally trigger this path. The explicit `TODO` comment in the production OS code confirms the check is known to be absent.

### Recommendation
In `execute_replace_class`, before updating `contract_state_changes`, verify that the requested `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared). Concretely:

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the check explicit and enforced at the point of replacement.

### Proof of Concept

1. **Deploy** a contract `VaultContract` that:
   - Accepts token deposits (stores balances in its storage)
   - Exposes a `freeze()` function that calls `replace_class(class_hash=0xdeadbeef)`

2. **Attract funds**: Users deposit tokens into `VaultContract`.

3. **Trigger the bug**: The attacker calls `VaultContract.freeze()`. The OS processes the `replace_class` syscall via `execute_replace_class` at `syscall_impls.cairo:877`. No validation of `0xdeadbeef` is performed. The state entry for `VaultContract` is updated with `class_hash=0xdeadbeef`.

4. **State committed**: The block is proven and committed. `VaultContract`'s class hash is now `0xdeadbeef` on-chain.

5. **Permanent freeze**: Any future transaction calling `VaultContract` causes `execute_entry_point` (`execute_entry_point.cairo:154–166`) to call `dict_read` on `contract_class_changes` with key `0xdeadbeef`, returning 0. `find_element` with key 0 finds no compiled class and panics. No valid proof can ever include a call to `VaultContract`. All deposited funds are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```
