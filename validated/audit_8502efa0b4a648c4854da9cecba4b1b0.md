### Title
Missing Nonce in `compute_meta_tx_v0_hash` Enables Within-Chain Replay of Meta-Transactions — (File: `transaction_hash/transaction_hash.cairo`)

---

### Summary

`compute_meta_tx_v0_hash` omits a nonce from the transaction hash, and `check_and_increment_nonce` explicitly skips nonce enforcement for version-0 transactions. The OS therefore provides zero protocol-level replay protection for `meta_tx_v0` calls. Any unprivileged attacker who observes a `meta_tx_v0` execution on-chain can replay the same signature indefinitely, leading to direct loss of funds from the victim account contract.

---

### Finding Description

**Root cause 1 — hash excludes nonce.**

`compute_meta_tx_v0_hash` in `transaction_hash/transaction_hash.cairo` (lines 295–314) delegates to `deprecated_get_transaction_hash` with `additional_data_size=0`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        ...
        chain_id=chain_id,
        additional_data_size=0,          // ← no nonce appended
        additional_data=cast(0, felt*),
    );
``` [1](#0-0) 

Compare with `compute_l1_handler_transaction_hash`, which correctly binds the nonce into the hash via `additional_data_size=1, additional_data=&nonce`: [2](#0-1) 

The resulting meta_tx_v0 hash is therefore fully determined by `(contract_address, calldata, chain_id)` — identical across every replay attempt.

**Root cause 2 — OS skips nonce enforcement for version 0.**

`check_and_increment_nonce` in `execute_transaction_utils.cairo` (lines 63–67) returns immediately for any version-0 `TxInfo`:

```cairo
func check_and_increment_nonce{contract_state_changes: DictAccess*}(tx_info: TxInfo*) -> () {
    // Do not handle nonce for version 0.
    if (tx_info.version == 0) {
        return ();
    }
``` [3](#0-2) 

**Root cause 3 — `execute_meta_tx_v0` hard-codes `nonce=0`.**

`execute_meta_tx_v0` in `syscall_impls.cairo` (lines 343–363) constructs the new `TxInfo` with `version=0` and `nonce=0`:

```cairo
tempvar new_tx_info = new TxInfo(
    version=0,
    ...
    nonce=0,
    ...
);
``` [4](#0-3) 

**Root cause 4 — `__validate__` is skipped for version 0.**

`run_validate` in `execute_transaction_utils.cairo` (lines 127–130) also bails out for version 0, so no OS-level signature gate exists either: [5](#0-4) 

The combined effect: the OS computes a deterministic, nonce-free hash, passes it to the account contract's `__execute__`, and performs no replay check of its own.

---

### Impact Explanation

An attacker who extracts the `(contract_address, calldata, signature)` triple from any on-chain `meta_tx_v0` execution can replay it by deploying a contract that issues the same `meta_tx_v0` syscall. The OS recomputes the identical hash, the account contract's `__execute__` receives the same `(transaction_hash, signature)` pair, and — unless the account contract independently tracks used hashes or embeds a nonce inside the calldata — the execution succeeds again.

Concrete impact: **direct loss of funds**. A single signed meta_tx_v0 authorising a token transfer can be replayed an unbounded number of times, draining the victim's balance. This satisfies the Critical impact tier.

---

### Likelihood Explanation

`meta_tx_v0` is the designated gasless-transaction mechanism for StarkNet. Relayer infrastructure will make these transactions common. All transaction data (calldata, signature) is public on-chain. The OS provides no protocol-level guard, so every account contract that relies on the OS-computed hash for replay protection is vulnerable. An attacker needs only to read the chain and submit a new outer transaction — no privileged access, no leaked key, no Sybil attack required.

---

### Recommendation

1. Add a per-account nonce to `compute_meta_tx_v0_hash` (analogous to how `compute_l1_handler_transaction_hash` appends the nonce via `additional_data`).
2. Enforce nonce checking and incrementing for meta_tx_v0 in `check_and_increment_nonce` (remove or condition the version-0 early return for this path).
3. Alternatively, include a caller-supplied nonce field in `MetaTxV0Request` and bind it into the hash, then verify and increment it in the OS.

---

### Proof of Concept

1. Alice signs a `meta_tx_v0` payload authorising `[transfer, Bob, 1000_STRK]` against her account contract on StarkNet mainnet. A relayer submits the outer transaction; it succeeds.
2. The OS computed hash = `H(INVOKE_PREFIX, 0, Alice_addr, EXECUTE_SELECTOR, H(calldata), 0, mainnet_chain_id)` — no nonce.
3. Eve observes the transaction on-chain and extracts `(Alice_addr, calldata, signature)`.
4. Eve deploys `ReplayContract` whose `__execute__` issues `meta_tx_v0(contract_address=Alice_addr, selector=EXECUTE_SELECTOR, calldata=calldata, signature=signature)`.
5. Eve submits a new outer invoke transaction (with her own nonce) calling `ReplayContract.__execute__`.
6. The OS enters `execute_meta_tx_v0`, computes the identical hash (same inputs, no nonce), constructs `TxInfo(version=0, nonce=0, transaction_hash=same_hash)`, and calls Alice's `__execute__` with the original signature.
7. Alice's `__execute__` verifies the signature against `tx_info.transaction_hash` — it matches — and executes the transfer again.
8. Eve repeats steps 5–7 until Alice's balance is zero. Each outer transaction costs Eve only her own gas; Alice bears the full financial loss.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L220-237)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L295-314)
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
