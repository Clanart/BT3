### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the supplied new class hash corresponds to a previously declared contract class. This is structurally identical to the ERC721 `_mint`-without-`_safeMint` pattern: an asset (a contract's callable identity, and therefore all funds it holds) is irrevocably transferred to a "recipient" (the new class hash) without first confirming the recipient is capable of handling it. Any contract can call `replace_class` with an arbitrary, undeclared hash, rendering itself permanently uncallable and freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS reads the requested new class hash from the syscall request and immediately writes it into `contract_state_changes` with no check that the hash is a declared class:

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

The TODO comment is the explicit acknowledgement of the missing guard. The state update is committed unconditionally.

When any subsequent transaction attempts to call the now-mutated contract, `execute_entry_point` performs:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — reads the compiled class hash for the (undeclared) class hash.
2. `find_element(array_ptr=compiled_class_facts_bundle.compiled_class_facts, ..., key=compiled_class_hash)` — searches the bundle of compiled class facts for that hash. [2](#0-1) 

`find_element` is a Cairo standard-library function that **panics** (fails the proof) if the key is absent. Because the class was never declared, the key will never be present. The sequencer is therefore forced to exclude every future call to the affected contract from any provable block. The contract becomes permanently uncallable.

The analog to the ERC721 report is exact:

| ERC721 report | StarkNet OS analog |
|---|---|
| `_mint` sends token to recipient without calling `onERC721Received` | `replace_class` sets class hash without checking it is declared |
| Recipient contract cannot handle ERC721 → token stuck | Contract class does not exist → contract permanently uncallable |
| Funds (NFT) frozen forever | Funds held by contract frozen forever |

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 tokens, ETH-bridged assets, or other value held in the storage of a contract that has replaced its class with an undeclared hash are irrecoverably frozen. There is no upgrade path, no escape hatch, and no L1 recovery mechanism for L2-held assets once the contract is uncallable.

---

### Likelihood Explanation

**Low-to-Medium.**

The `replace_class` syscall is available to every Sierra contract with no privilege restriction. An attacker can:

1. Deploy a contract that appears legitimate (e.g., a vault or token bridge).
2. Attract user deposits.
3. Call `replace_class` with an arbitrary undeclared felt value as the class hash.
4. The OS accepts the state change; the contract is permanently bricked.

Alternatively, a buggy contract that passes an incorrect hash to `replace_class` triggers the same outcome accidentally. No privileged role, leaked key, or external dependency is required — only the ability to submit a transaction that calls `replace_class`.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists as a key in `contract_class_changes` (the dictionary of declared classes). The check should mirror the lookup already performed in `execute_entry_point`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This is the direct analog of using `_safeMint` (which calls `onERC721Received`) instead of `_mint`: confirm the "recipient" (new class) is capable of handling the role before committing the state change.

---

### Proof of Concept

1. **Setup**: Attacker deploys `MaliciousVault` — a Sierra contract that (a) accepts token deposits and (b) exposes a `brick_self` entry point that calls `replace_class(0xdeadbeef)`.
2. **Attract funds**: Users deposit tokens into `MaliciousVault`. The contract's storage now holds balances.
3. **Trigger**: Attacker calls `brick_self`. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`.
4. **No validation**: The TODO guard is absent; `dict_update` writes `class_hash=0xdeadbeef` into `contract_state_changes` unconditionally. [3](#0-2) 

5. **Block committed**: The block is proven and the state root is updated on L1. `MaliciousVault`'s class hash is now `0xdeadbeef` on-chain.
6. **Permanent freeze**: Any subsequent `call_contract` or `invoke` targeting `MaliciousVault` reaches `execute_entry_point`, which calls `find_element` for compiled class hash of `0xdeadbeef`. The key is absent from `compiled_class_facts_bundle`; `find_element` panics; the block is unprovable. The sequencer must permanently exclude all calls to `MaliciousVault`. All deposited tokens are frozen forever. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
