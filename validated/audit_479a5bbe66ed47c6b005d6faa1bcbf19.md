### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Unbounded Signature Replay on Meta Transactions - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` omits a nonce (or any replay-preventing unique identifier) from the signed data. Because the OS also explicitly skips nonce enforcement for version-0 transactions, a valid meta-transaction signature is permanently reusable for the same `(contract_address, calldata, chain_id)` tuple. Any unprivileged contract can replay the signature indefinitely, draining the target account.

---

### Finding Description

`compute_meta_tx_v0_hash` in `transaction_hash/transaction_hash.cairo` delegates to `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`:

```cairo
// transaction_hash.cairo lines 295-315
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,          // ← no nonce
        additional_data=cast(0, felt*),  // ← no nonce
    );
    return tx_hash;
}
```

The resulting hash is fully determined by `(INVOKE_HASH_PREFIX, 0, contract_address, selector, H(calldata), 0, chain_id)` — a static tuple that never changes between invocations.

Contrast this with `compute_l1_handler_transaction_hash`, which passes `additional_data_size=1, additional_data=&nonce` to bind each execution to a unique nonce:

```cairo
// transaction_hash.cairo lines 220-238
func compute_l1_handler_transaction_hash{pedersen_ptr: HashBuiltin*}(
    execution_context: ExecutionContext*, chain_id: felt, nonce: felt
) -> felt {
    ...
    additional_data_size=1,
    additional_data=&nonce,   // ← nonce present
    ...
}
```

The OS-level nonce guard in `check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
// execute_transaction_utils.cairo lines 63-67
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
    ...
}
```

`execute_meta_tx_v0` in `syscall_impls.cairo` constructs the new `TxInfo` with `version=0` and `nonce=0`, then passes `meta_tx_hash` (the nonce-free hash) as `transaction_hash`:

```cairo
// syscall_impls.cairo lines 343-363
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    transaction_hash=meta_tx_hash,   // ← nonce-free hash
    nonce=0,                         // ← always zero
    ...
);
```

The target account's `__validate__` function receives this `transaction_hash` and verifies the caller-supplied signature against it. Because the hash is identical for every replay of the same `(contract_address, calldata, chain_id)`, any signature that was valid once remains valid forever.

There is no caller restriction on who may invoke the `META_TX_V0_SELECTOR` syscall:

```cairo
// execute_syscalls.cairo lines 343-344
assert selector = META_TX_V0_SELECTOR;
execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```

---

### Impact Explanation

**Critical — Direct loss of funds.**

A valid meta-transaction signature authorising, for example, an ERC-20 transfer of N tokens from account A to address X can be replayed by any unprivileged contract an unlimited number of times. Each replay executes `__execute__` on account A with the original calldata, transferring N tokens again. The account is drained until its balance reaches zero. No privileged access is required; the attacker only needs to observe one legitimate meta-transaction.

---

### Likelihood Explanation

**High.** The `meta_tx_v0` syscall is callable by any deployed contract with no access control. Signatures for meta-transactions are observable on-chain (they appear in the `MetaTxV0Request` struct written to the syscall segment). A v0 account contract's `__validate__` function performs ECDSA against `tx_info.transaction_hash`; because the OS supplies a nonce-free hash, the account has no OS-level replay protection to rely on. Any account that does not implement its own application-level replay guard (e.g., an internal nonce stored in its own storage) is immediately exploitable.

---

### Recommendation

Include a nonce in the meta-transaction hash, mirroring the pattern used by `compute_l1_handler_transaction_hash`. The nonce should be read from the target account's on-chain state and incremented by the OS after each successful meta-transaction execution, exactly as `check_and_increment_nonce` does for version ≥ 1 transactions.

```cairo
// Proposed fix in compute_meta_tx_v0_hash:
additional_data_size=1,
additional_data=&nonce,   // nonce fetched from contract_state_changes[contract_address]
```

The OS must also increment the target account's nonce after each `meta_tx_v0` execution to prevent replay.

---

### Proof of Concept

1. **Legitimate use:** A bundler contract B calls `meta_tx_v0` targeting account A with calldata `[transfer(X, 100)]` and a valid ECDSA signature S. The OS computes `H = compute_meta_tx_v0_hash(A, __execute__, [transfer(X,100)], chain_id)`. Account A's `__validate__` verifies S against H — passes. 100 tokens are transferred to X.

2. **Replay:** Attacker deploys contract C. C calls `meta_tx_v0` with the identical parameters `(A, __execute__, [transfer(X,100)], S)`. The OS computes the same H (no nonce in the hash, no nonce increment happened). Account A's `__validate__` verifies S against H — passes again. Another 100 tokens are transferred to X.

3. **Loop:** C repeats step 2 in a loop (or across multiple transactions) until A's balance is zero.

**Root cause location:** [1](#0-0) 

**Nonce bypass location:** [2](#0-1) 

**Syscall construction (nonce=0, version=0):** [3](#0-2) 

**Unrestricted syscall dispatch:** [4](#0-3) 

**Contrast — L1 handler correctly includes nonce:** [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L220-238)
```text
func compute_l1_handler_transaction_hash{pedersen_ptr: HashBuiltin*}(
    execution_context: ExecutionContext*, chain_id: felt, nonce: felt
) -> felt {
    let (__fp__, _) = get_fp_and_pc();
    let (transaction_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=L1_HANDLER_HASH_PREFIX,
        version=L1_HANDLER_VERSION,
        contract_address=execution_context.execution_info.contract_address,
        entry_point_selector=execution_context.execution_info.selector,
        calldata_size=execution_context.calldata_size,
        calldata=execution_context.calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=1,
        additional_data=&nonce,
    );

    return transaction_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-315)
```text
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-363)
```text
    tempvar new_tx_info = new TxInfo(
        version=0,
        account_contract_address=contract_address,
        max_fee=0,
        signature_start=request.signature_start,
        signature_end=request.signature_end,
        transaction_hash=meta_tx_hash,
        chain_id=old_tx_info.chain_id,
        nonce=0,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L343-344)
```text
    assert selector = META_TX_V0_SELECTOR;
    execute_meta_tx_v0(block_context=block_context, caller_execution_context=execution_context);
```
