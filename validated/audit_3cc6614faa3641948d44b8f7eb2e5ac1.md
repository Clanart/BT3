### Title
`compute_meta_tx_v0_hash` Omits Caller Address and Nonce, Enabling Unbounded Signature Replay — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` computes the meta-transaction hash without binding it to the submitting caller's address or any nonce. Because the hash is the only thing a target account contract can validate a signature against, any attacker who observes a valid meta-tx v0 signature on-chain can replay it — from any caller contract, any number of times — causing repeated unauthorized execution of the signed action and direct loss of funds.

---

### Finding Description

`execute_meta_tx_v0` in `syscall_impls.cairo` is a syscall that lets any contract execute another contract's `__execute__` entry point under a fresh `TxInfo` with `version=0`, `nonce=0`, and a caller-supplied signature. The transaction hash that the target contract sees is produced by `compute_meta_tx_v0_hash`:

```cairo
// transaction_hash/transaction_hash.cairo L295-L315
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
        additional_data=cast(0, felt*),  // ← no caller address
    );
    return tx_hash;
}
```

Two critical omissions:

1. **No caller address in the hash.** The address of the contract invoking the syscall is never mixed into the hash. Any other contract can supply the identical `(contract_address, selector, calldata, chain_id)` tuple and obtain the same hash, making the original user's signature valid for their call too.

2. **No nonce and no nonce enforcement.** The resulting `TxInfo` is constructed with `nonce=0`. `check_and_increment_nonce` explicitly skips version-0 transactions:

```cairo
// execute_transaction_utils.cairo L65-L67
if (tx_info.version == 0) {
    return ();
}
```

So the target contract's on-chain nonce is never incremented and the same hash/signature pair is valid for every future replay.

The `TxInfo` written into the execution context confirms both omissions:

```cairo
// syscall_impls.cairo L343-L363
tempvar new_tx_info = new TxInfo(
    version=0,
    account_contract_address=contract_address,
    max_fee=0,
    signature_start=request.signature_start,
    signature_end=request.signature_end,
    transaction_hash=meta_tx_hash,   // hash with no caller, no nonce
    chain_id=old_tx_info.chain_id,
    nonce=0,                         // always zero, never checked
    ...
);
```

Additionally, `run_validate` is skipped for version-0 transactions, so the OS itself performs no signature check; the burden falls entirely on the target contract's `__execute__`, which can only validate against the unbound hash.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A user signs a meta-tx v0 authorizing a token transfer (or any state-mutating action). The signature commits only to `(contract_address, selector, calldata, chain_id)`. Once the first relayer broadcasts the outer transaction, the signature is public. Any attacker can:

1. Copy the `(contract_address, selector, calldata, signature)` tuple.
2. Submit their own outer transaction invoking `meta_tx_v0` with those parameters.
3. The OS recomputes the identical hash; the target contract's signature check passes.
4. The signed action (e.g., ERC-20 transfer) executes again.
5. Repeat indefinitely — there is no nonce barrier.

Every replay drains the victim's balance by the amount specified in the calldata, constituting direct, unbounded loss of funds.

---

### Likelihood Explanation

**High.** The attack requires only:
- Monitoring the public mempool or on-chain history for any `meta_tx_v0` syscall.
- Copying the signature and parameters verbatim.
- Submitting a new outer transaction — no privileged access, no leaked key, no Sybil attack.

The syscall is reachable by any unprivileged contract deployer or transaction sender. The absence of nonce enforcement means the window of exploitation is permanent once a signature is published.

---

### Recommendation

1. **Bind the hash to the caller.** Include the address of the contract invoking the syscall (`caller_execution_info.contract_address`) as a field in `compute_meta_tx_v0_hash`, analogous to how `sender_address` is included in `hash_tx_common_fields` for v3 transactions.

2. **Enforce a per-target nonce.** Either pass a caller-supplied nonce through `additional_data` and enforce it in the OS (as `check_and_increment_nonce` does for v1/v3), or require the target contract to maintain and check its own replay counter.

3. **Alternatively, adopt a two-step commit/reveal scheme** so the signature is committed in one block and revealed in another, preventing front-running even if the hash is observed.

---

### Proof of Concept

**Setup:** `TokenContract` at address `T` holds user funds. User signs a meta-tx authorizing `transfer(attacker, 100)`.

**Step 1 — Legitimate relay:**
- Relayer deploys `RelayerContract` and calls `meta_tx_v0(contract_address=T, selector=EXECUTE, calldata=[transfer, attacker, 100], signature=S)`.
- OS computes `H = hash(INVOKE_PREFIX, 0, T, EXECUTE, [transfer,attacker,100], 0, chain_id)`.
- `T.__execute__` validates `S` against `H` → passes. Transfer executes.

**Step 2 — Attacker replay (unbounded):**
- Attacker deploys `AttackerContract` and calls `meta_tx_v0` with the same parameters and `S` (copied from chain).
- OS recomputes the identical `H` (caller address absent, nonce absent).
- `T.__execute__` validates `S` against `H` → passes again. Another transfer executes.
- Attacker repeats until the user's balance is zero.

Root cause confirmed at: [1](#0-0) 

Nonce bypass confirmed at: [2](#0-1) 

Caller set to `ORIGIN_ADDRESS` (not the invoking contract) confirmed at: [3](#0-2)

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
