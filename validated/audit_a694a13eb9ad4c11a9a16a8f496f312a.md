### Title
Meta-Transaction v0 Hash Lacks Nonce — Signature Replay Enables Repeated Unauthorized Execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` omits a nonce from the signed hash, and the OS execution path for version-0 transactions explicitly skips nonce checking. Any meta-tx v0 signature observed on-chain can be replayed an unlimited number of times by an unprivileged attacker, causing repeated unauthorized execution of the signed calldata and direct loss of funds.

---

### Finding Description

`compute_meta_tx_v0_hash` builds the transaction hash using `deprecated_get_transaction_hash` with `additional_data_size=0` and `additional_data=cast(0, felt*)`: [1](#0-0) 

The resulting hash is a pure function of `(INVOKE_HASH_PREFIX, version=0, contract_address, entry_point_selector, calldata, max_fee=0, chain_id)`. No nonce, block number, or any other per-execution uniqueness factor is included.

In `execute_meta_tx_v0`, the synthesized `new_tx_info` is constructed with `version=0` and `nonce=0` hardcoded: [2](#0-1) 

The nonce enforcement function `check_and_increment_nonce` explicitly returns early for any version-0 transaction, performing no state update and no uniqueness check: [3](#0-2) 

These two facts together mean: the hash signed by the account owner is identical across every replay, and the OS never increments or validates a nonce to prevent re-use.

Compare with `compute_l1_handler_transaction_hash`, which correctly passes `additional_data_size=1, additional_data=&nonce` to bind the hash to a unique nonce: [4](#0-3) 

The meta-tx v0 path has no equivalent protection.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If the signed calldata encodes a token transfer or any fund-moving operation (the primary use-case for meta-transactions), an attacker who observes the signature once can replay it indefinitely. Each replay passes `__validate__` because the hash is identical, and the OS never rejects it for nonce reasons. The victim's account is drained until it is empty or the account contract is upgraded.

---

### Likelihood Explanation

**High.** All meta-tx v0 signatures are visible on-chain the moment the first execution is included in a block. Any unprivileged party can immediately construct a new transaction that invokes the `meta_tx_v0` syscall with the same `(contract_address, selector, calldata, signature)` tuple. No special access, key material, or privileged role is required. The attacker only needs to read the chain and submit a transaction.

---

### Recommendation

Include a per-account, monotonically incrementing nonce in the meta-tx v0 hash, mirroring the pattern used by `compute_l1_handler_transaction_hash`. Concretely:

1. In `compute_meta_tx_v0_hash`, add a `nonce: felt` parameter and pass `additional_data_size=1, additional_data=&nonce`.
2. In `execute_meta_tx_v0`, read the target account's current nonce from `contract_state_changes`, pass it to the hash function, and call `check_and_increment_nonce` (or an equivalent) to atomically consume it — this requires treating the meta-tx as a non-version-0 transaction for nonce purposes, or introducing a dedicated nonce slot.
3. Alternatively, bind the hash to the outer transaction's nonce so that each outer transaction can only produce one valid meta-tx execution. [1](#0-0) 

---

### Proof of Concept

**Setup**: Account `A` holds 1000 STRK. A relayer submits a meta-tx v0 on behalf of `A` to transfer 100 STRK to address `B`. The signed hash is:

```
H = Pedersen(
  "invoke", 0, A, __execute__, calldata=[transfer(B,100)], 0, chain_id
)
```

**Step 1 — Observe**: Attacker reads the first successful transaction from the chain, extracting `(contract_address=A, selector=__execute__, calldata, signature)`.

**Step 2 — Replay**: Attacker deploys contract `Evil` containing:

```cairo
// pseudocode
func attack():
    syscall meta_tx_v0(
        contract_address = A,
        selector         = __execute__,
        calldata         = [transfer(B, 100)],
        signature        = <observed signature>
    )
```

**Step 3 — OS processing**: `execute_meta_tx_v0` computes the same hash `H` (no nonce in hash), constructs `new_tx_info` with `version=0, nonce=0`. `check_and_increment_nonce` returns immediately because `version == 0`. Account `A`'s `__validate__` verifies the signature against `H` — it matches. `__execute__` runs, transferring another 100 STRK to `B`.

**Step 4 — Repeat**: Attacker repeats Step 2 nine more times. Account `A` loses all 1000 STRK.

The OS never rejects any replay because `compute_meta_tx_v0_hash` produces the same value every time: [5](#0-4) 

and `check_and_increment_nonce` is a no-op for version 0: [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L343-352)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L63-67)
```text
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
```
