### Title
Incomplete "Uninitialized" Check in `deploy_contract` Allows Deployment to Address with Pre-Existing Storage - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

---

### Summary

`deploy_contract` checks only `class_hash == 0` and `nonce == 0` to assert a target address is uninitialized before deploying. It does not verify that the address has no pre-existing storage. Because `execute_replace_class` in `deprecated_execute_syscalls.cairo` imposes no lower bound on the new class hash (allowing `replace_class(0)`), an attacker can manufacture an address that passes both checks while carrying attacker-controlled storage. A victim's contract deployed to that address inherits the poisoned storage, enabling direct loss of funds.

---

### Finding Description

**Incomplete emptiness check in `deploy_contract`**

`deploy_contract` enforces two conditions before allowing deployment:

```cairo
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;   // line 53
assert state_entry.nonce = 0;                                // line 54
``` [1](#0-0) 

`UNINITIALIZED_CLASS_HASH` is the constant `0`. [2](#0-1) 

The `StateEntry` struct has three fields: `class_hash`, `storage_ptr`, and `nonce`. [3](#0-2) 

The protocol's own definition of an "empty" contract in `get_contract_state_hash` requires **all three** to be zero — `class_hash == 0`, `storage_root == 0`, and `nonce == 0` — before returning a leaf hash of `0`:

```cairo
if (class_hash == UNINITIALIZED_CLASS_HASH) {
    if (storage_root == 0) {
        if (nonce == 0) {
            return (hash=0);
``` [4](#0-3) 

`deploy_contract` checks only two of the three conditions, omitting the storage-root check. This is the direct analog of the EIP-161 / EIP-1052 discrepancy in the original report.

**`execute_replace_class` allows `class_hash = 0`**

`execute_replace_class` accepts any caller-supplied class hash with no lower-bound validation:

```cairo
func execute_replace_class{...}(contract_address, syscall_ptr: ReplaceClass*) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;
    ...
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
``` [5](#0-4) 

There is no `assert class_hash != 0` or equivalent guard. A deployed contract can therefore call `replace_class(0)`, resetting its `class_hash` to `UNINITIALIZED_CLASS_HASH` while leaving `storage_ptr` and `nonce` intact.

**Storage is inherited on deployment**

When `deploy_contract` creates the new state entry it explicitly carries forward `state_entry.storage_ptr`:

```cairo
tempvar new_state_entry = new StateEntry(
    class_hash=constructor_execution_context.class_hash,
    storage_ptr=state_entry.storage_ptr,   // ← inherited
    nonce=0,
);
``` [6](#0-5) 

Any storage written before the deployment is silently adopted by the newly deployed contract.

---

### Impact Explanation

**Direct loss of funds (Critical).**

A victim deploys a financial contract (token, vault, multisig) to a deterministic address. The attacker pre-populates that address's storage with attacker-controlled values (e.g., `balances[attacker] = MAX_UINT`, `owner = attacker`). Because `deploy_contract` does not check storage emptiness, the victim's constructor runs against the poisoned storage. The attacker can then drain the contract immediately after deployment.

---

### Likelihood Explanation

The contract address in StarkNet is deterministic:

```
address = H(PREFIX, deployer_address, salt, class_hash, H(constructor_calldata))
``` [7](#0-6) 

An attacker who observes a pending `deploy_account` or `deploy` syscall transaction in the mempool can extract `class_hash`, `salt`, `constructor_calldata`, and `deployer_address`, compute the target address, and execute the three-step attack before the victim's transaction is sequenced. The attack requires no privileged role — only the ability to submit transactions.

---

### Recommendation

1. **In `deploy_contract`**: add an assertion that the storage dictionary for the target address is empty (i.e., `storage_ptr` points to an empty segment / no prior entries exist), mirroring the full three-field emptiness definition used in `get_contract_state_hash`.

2. **In `execute_replace_class`**: add `assert_not_zero(class_hash)` (or equivalent) to prevent any contract from resetting its class hash to `UNINITIALIZED_CLASS_HASH = 0`, which is the enabler of the storage-poisoning primitive.

---

### Proof of Concept

1. Victim intends to deploy contract `V` with `class_hash=H`, `salt=S`, `deploy_from_zero=True`. The deterministic address is `X = f(H, S, 0, calldata)`.

2. Attacker observes the pending transaction, computes `X`, and submits a deploy transaction with identical parameters. Attacker's contract `A` is deployed at `X`.

3. Contract `A` calls `storage_write(key=BALANCE_SLOT, value=ATTACKER_BALANCE)` to write a large balance for the attacker's address.

4. Contract `A` calls `replace_class(0)`. `execute_replace_class` sets `class_hash=0`, `nonce` remains `0`, `storage_ptr` retains the written entry. Address `X` now satisfies both checks in `deploy_contract`: `class_hash == UNINITIALIZED_CLASS_HASH` and `nonce == 0`.

5. Victim's deploy transaction is processed. `deploy_contract` passes both assertions at lines 53–54. The new `StateEntry` is created with `storage_ptr=state_entry.storage_ptr` (the poisoned storage). Victim's constructor runs but does not overwrite `BALANCE_SLOT`.

6. Attacker calls `transfer` on the newly deployed token contract and drains the victim's funds using the pre-set balance.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L56-60)
```text
    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L16-16)
```text
const UNINITIALIZED_CLASS_HASH = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L55-61)
```text
    if (class_hash == UNINITIALIZED_CLASS_HASH) {
        if (storage_root == 0) {
            if (nonce == 0) {
                return (hash=0);
            }
        }
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_address/contract_address.cairo (L12-35)
```text
func get_contract_address{hash_ptr: HashBuiltin*, range_check_ptr}(
    salt: felt,
    class_hash: felt,
    constructor_calldata_size: felt,
    constructor_calldata: felt*,
    deployer_address: felt,
) -> (contract_address: felt) {
    let (hash_state_ptr) = hash_init();
    let (hash_state_ptr) = hash_update_single(
        hash_state_ptr=hash_state_ptr, item=CONTRACT_ADDRESS_PREFIX
    );
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=deployer_address);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=salt);
    let (hash_state_ptr) = hash_update_single(hash_state_ptr=hash_state_ptr, item=class_hash);
    let (hash_state_ptr) = hash_update_with_hashchain(
        hash_state_ptr=hash_state_ptr,
        data_ptr=constructor_calldata,
        data_length=constructor_calldata_size,
    );
    let (contract_address_before_modulo) = hash_finalize(hash_state_ptr=hash_state_ptr);
    let (contract_address) = normalize_address(addr=contract_address_before_modulo);

    return (contract_address=contract_address);
}
```
