### Title
`meta_tx_v0` Syscall Computes a Nonce-Free Transaction Hash, Enabling Signature Replay Against Any Account Contract's `__execute__` - (`crates/blockifier/src/execution/syscalls/syscall_base.rs`, `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `meta_tx_v0` syscall constructs a synthetic `TxInfo` with a hardcoded `nonce = 0` and computes a transaction hash that includes no nonce field (`additional_data_size = 0`). The OS also explicitly skips nonce checking and incrementing for version-0 transactions. As a result, any signature produced for a `meta_tx_v0` call covers only `(contract_address, entry_point_selector, calldata, chain_id)` — a static tuple that never changes. An attacker who observes a valid `meta_tx_v0` signature on-chain can replay it in any subsequent outer transaction, causing the target account's `__execute__` to run again with the same signature and calldata, producing unauthorized state changes that are committed to the block and proven as valid.

---

### Finding Description

**Root cause — hash computation omits nonce:**

`compute_meta_tx_v0_hash` in the Cairo OS calls `deprecated_get_transaction_hash` with `additional_data_size=0`, meaning no nonce is hashed:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,          // ← only replay-domain field
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        additional_data_size=0,   // ← NO nonce
        additional_data=cast(0, felt*),
    );
``` [1](#0-0) 

The Rust blockifier mirrors this exactly, hardcoding `nonce: Nonce(0.into())` in the synthetic `TransactionInfo` and computing the hash via `InvokeTransactionV0` (which also passes no nonce):

```rust
let new_tx_info = TransactionInfo::Deprecated(DeprecatedTransactionInfo {
    common_fields: CommonAccountFields {
        transaction_hash,
        version: TransactionVersion::ZERO,
        signature,
        nonce: Nonce(0.into()),   // ← hardcoded, never incremented
        sender_address: contract_address,
        only_query,
    },
    max_fee: Fee(0),
});
``` [2](#0-1) 

**Root cause — nonce check is explicitly skipped for version 0:**

`check_and_increment_nonce` in the Cairo OS returns immediately for any `version == 0` transaction, so the account contract's on-chain nonce is never read or incremented during a `meta_tx_v0` call:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

**Root cause — `__validate__` is also skipped for version 0:**

`run_validate` in the Cairo OS returns immediately for `version == 0`, so the account contract's signature-checking entry point is never invoked by the OS:

```cairo
// Do not run "__validate__" for version 0.
if (tx_execution_info.tx_info.version == 0) {
    return ();
}
``` [4](#0-3) 

**Execution path — `execute_meta_tx_v0` in the Cairo OS:**

The OS constructs the synthetic `TxInfo` with `nonce=0`, then calls `contract_call_helper` which directly invokes `__execute__` on the target contract. No nonce is checked, no nonce is incremented, and `__validate__` is not called: [5](#0-4) 

**What the account contract sees:**

The target account contract's `__execute__` receives a `TxInfo` where `transaction_hash = H(contract_address, selector, calldata, chain_id)` and `nonce = 0`. The standard Starknet account pattern is to verify the user's ECDSA signature against `transaction_hash`. Because `transaction_hash` is a pure function of static inputs (no nonce, no block number, no timestamp), the same signature is valid for every replay. [6](#0-5) 

---

### Impact Explanation

**Impact: Critical — Wrong state from blockifier/syscall/execution logic for accepted input.**

An attacker who observes a `meta_tx_v0` call in any committed block can extract `(contract_address, selector, calldata, signature)` from the outer transaction's calldata. They then submit a new outer transaction (with their own nonce) that calls `meta_tx_v0` with the same parameters. The OS accepts this because:

1. The outer transaction has a valid nonce (the attacker's own).
2. The `meta_tx_v0` hash is identical to the original (same static inputs).
3. The account contract's `__execute__` receives the same hash and signature, passes its own signature check, and executes again.

Each replay produces a new state diff (e.g., token transfer, storage write) that is committed to the block, included in the state root, and proven valid by the ZK proof. The victim's account state is mutated without their authorization on every replay, until their balance or allowance is exhausted.

The attacker's only cost is gas for the outer transaction. The victim has no on-chain mechanism to revoke a `meta_tx_v0` signature after it has been used once, because the OS provides no nonce tracking for it.

---

### Likelihood Explanation

**Likelihood: High.**

- `meta_tx_v0` is a production syscall (present in versioned constants diff for 0.13.6→0.14.0).
- Any relayer contract that uses `meta_tx_v0` exposes user signatures on-chain in the outer transaction's calldata, making them trivially extractable.
- The standard Starknet account contract pattern (verify ECDSA against `tx_info.transaction_hash`) provides no replay protection when the hash is nonce-free.
- No privileged access is required; any unprivileged address can submit the replaying outer transaction.

---

### Recommendation

Include a per-account nonce in the `meta_tx_v0` hash. The OS should:

1. Read the current nonce from `contract_state_changes` for the target `contract_address` before computing the hash.
2. Pass it as `additional_data` to `deprecated_get_transaction_hash` (i.e., `additional_data_size=1`, `additional_data=&current_nonce`).
3. Increment the nonce in `contract_state_changes` after the call, analogous to `check_and_increment_nonce` for non-v0 transactions.

In the Rust blockifier, `SyscallBaseHandler::meta_tx_v0` should read `self.state.get_nonce_at(contract_address)?`, include it in the `InvokeTransactionV0` hash computation (or a separate poseidon hash), and call `self.state.increment_nonce(contract_address)?` after successful execution.

---

### Proof of Concept

1. Deploy an account contract `A` whose `__execute__` transfers `N` tokens to a recipient when called with specific calldata, verifying the signature against `tx_info.transaction_hash`.
2. Deploy a relayer contract `R` that exposes `execute_meta_tx_v0(address, selector, calldata, signature)`.
3. User signs `H(A, __execute__, calldata, chain_id)` and submits outer tx T1 via `R.execute_meta_tx_v0(A, __execute__, calldata, sig)`. Tokens transfer. T1 is committed.
4. Attacker reads `(A, __execute__, calldata, sig)` from T1's calldata.
5. Attacker submits outer tx T2 (with attacker's own nonce) calling `R.execute_meta_tx_v0(A, __execute__, calldata, sig)`.
6. OS computes the same `meta_tx_v0` hash (nonce-free), constructs `TxInfo` with `nonce=0`, calls `A.__execute__`. `A` verifies the signature against the same hash — passes. Tokens transfer again.
7. Repeat step 5–6 until `A`'s balance is drained. Each iteration produces a valid state diff committed to the block and proven by the ZK proof.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L317-328)
```rust
        // Compute meta-transaction hash.
        let transaction_hash = InvokeTransactionV0 {
            max_fee: Fee(0),
            signature: signature.clone(),
            contract_address,
            entry_point_selector,
            calldata,
        }
        .calculate_transaction_hash(
            &self.context.tx_context.block_context.chain_info.chain_id,
            &signed_tx_version(&TransactionVersion::ZERO, &TransactionOptions { only_query }),
        )?;
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L333-343)
```rust
        let new_tx_info = TransactionInfo::Deprecated(DeprecatedTransactionInfo {
            common_fields: CommonAccountFields {
                transaction_hash,
                version: TransactionVersion::ZERO,
                signature,
                nonce: Nonce(0.into()),
                sender_address: contract_address,
                only_query,
            },
            max_fee: Fee(0),
        });
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L127-130)
```text
    // Do not run "__validate__" for version 0.
    if (tx_execution_info.tx_info.version == 0) {
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
