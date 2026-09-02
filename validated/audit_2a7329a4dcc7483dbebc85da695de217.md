### Title
Unchecked `u32` multiplication in withdrawal storage-key derivation can decouple a claimed deposit's move-to-vault identity from its verified withdrawal payout - ([File: circuits-lib/src/bridge_circuit/storage_proof.rs])

### Summary
`verify_storage_proofs` derives the Ethereum storage keys used to authenticate a withdrawal's UTXO/vout and the corresponding deposit's move-to-vault txid from a single, prover-supplied `index: u32` field. The withdrawal UTXO/vout keys are computed as `storage_address + U256::from(storage_proof.index * 2)` / `... * 2 + 1`, where the multiplication is performed in native `u32` arithmetic **before** widening to `U256`, exactly mirroring the reported Vyper `slice()` flaw where `start + length` is computed in a narrow, overflow-prone type before the bounds check.

### Finding Description [1](#0-0) 

```
let storage_key_utxo = storage_address + U256::from(storage_proof.index * 2);
let storage_key_vout = storage_address + U256::from(storage_proof.index * 2 + 1);
...
let deposit_storage_key = storage_address_deposit + U256::from(storage_proof.index);
```

`storage_proof.index` is a plain `u32` field of the untrusted, host-supplied `StorageProof` struct [2](#0-1) . The deposit-txid key uses `index` directly (no overflow possible for realistic values), but the UTXO/vout keys are derived from `index * 2` computed in `u32` space. If `index >= 2^31`, `index * 2` wraps around in `u32` arithmetic (Rust release builds do not panic on overflow), producing a storage key that actually points at a *different, smaller* deposit index `M = (index * 2 mod 2^32) / 2`, while the deposit-txid key still faithfully points at the prover's real, large `index`.

Because the three sub-proofs (UTXO, vout, deposit-txid) are verified independently against the same state root and never cross-checked for consistency with a common index, an attacker can decouple them: submit a genuine SMT proof for their own deposit's `move_txid` at large index `N`, but reuse a genuine, already-published SMT proof for the withdrawal UTXO/vout belonging to a *different* deposit `M` whose withdrawal was already fronted by an honest operator. The circuit will accept both proofs as valid (each is a real Merkle inclusion proof against the true state root), and will return `(user_wd_outpoint, vout, move_txid)` where `move_txid` belongs to deposit `N` but `user_wd_outpoint`/`vout` belong to deposit `M`.

Downstream, `bridge_circuit` only checks that the *returned* `user_wd_txid`/`vout` match the payout transaction's referenced input [3](#0-2) ; it never re-derives or checks that the same on-chain `index` produced all three storage keys. Since deposit `M`'s withdrawal was already publicly fulfilled by its rightful operator (a real, previously-broadcast Bitcoin payout transaction), the attacker can point their `payout_spv` at that pre-existing transaction and pass SPV verification, while binding the whole proof's `deposit_constant`/journal to their own kickoff/move-to-vault data for deposit `N` [4](#0-3) .

This breaks the intended equality: *the withdrawal actually fronted by an operator == the withdrawal used to justify that operator's reimbursement for its own move-to-vault UTXO*. An attacker who controls a kickoff for deposit `N` could claim/justify reimbursement using a withdrawal payout it never funded (deposit `M`'s), by presenting index `N` (whose binary value happens to make `2N` wrap to `2M`).

### Impact Explanation
If exploitable, this would let an operator be reimbursed for a payout it never funded — a Critical-severity custody-binding violation per the given impact taxonomy, since the deposit-index binding meant to tie a specific withdrawal fulfillment to a specific deposit/kickoff can be spoofed via integer overflow instead of an honest 1:1 correspondence.

### Likelihood Explanation
Likelihood is low in current practice: `index` is the sequential deposit counter maintained by the Citrea bridge contract [5](#0-4) , so triggering the overflow (`index >= 2^31`, i.e. `index * 2` wrapping past `u32::MAX`) requires the bridge to have processed roughly 2.1 billion deposits, which is not realistically achievable today. The underlying arithmetic defect is nonetheless real, matches the exact overflow-bypasses-bounds-check pattern from the reported bug class, and would become directly exploitable as the deposit counter grows, or immediately exploitable if the contract's deposit index type/policy ever changes.

### Recommendation
Compute all storage-key offsets in `U256` from the start (as is already done in `deposit_storage_key`), e.g. `storage_address + (U256::from(storage_proof.index) * U256::from(2))`, so no intermediate `u32` multiplication can wrap. Additionally, add an explicit consistency check binding all three sub-proofs to the same claimed `index` (e.g., verify the deposit's `index` against the caller-provided/committed deposit index used elsewhere in the circuit, such as the kickoff data) rather than trusting three independently-keyed proofs implicitly.

### Proof of Concept
1. Wait until (or assume) the Citrea bridge contract's deposit counter has advanced to `M` real deposits and an honest operator has already fulfilled withdrawal `M` with a real Bitcoin payout transaction `P_M`.
2. As an attacker with a kickoff for deposit `N = 2^31 + M`, request `get_storage_proof(l2_height, N)` — this yields a valid SMT proof for `deposit_storage_key = base + N` (attacker's real move_txid) but the same call structure allows substituting the independently obtained, equally valid SMT proofs for `storage_key_utxo`/`storage_key_vout` at `base + 2M` / `base + 2M + 1` (deposit `M`'s already-public withdrawal data), since `2N mod 2^32 == 2M`.
3. Submit a `BridgeCircuitInput` with `sp.index = N`, the deposit-txid proof for `N`, but the UTXO/vout proofs for `M`, and `payout_spv` pointing at the already-broadcast `P_M`.
4. `verify_storage_proofs` accepts all three proofs (each is independently valid against the true state root) and returns `move_txid` for `N` paired with the withdrawal UTXO/vout for `M`; `bridge_circuit`'s later checks only compare the returned `vout`/`user_wd_txid` against `P_M`'s input, which matches, so the whole circuit succeeds — falsely proving that the attacker's deposit `N` was paid out via `P_M`, which they never funded.

### Citations

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L69-85)
```rust
    let storage_key_utxo: alloy_primitives::Uint<256, 4> =
        storage_address + U256::from(storage_proof.index * 2);

    let storage_key_vout: alloy_primitives::Uint<256, 4> =
        storage_address + U256::from(storage_proof.index * 2 + 1);

    let storage_address_deposit: U256 = {
        let mut keccak = Keccak256::new();
        keccak.update(DEPOSIT_STORAGE_INDEX);
        let hash = keccak.finalize();
        U256::from_be_bytes(
            <[u8; 32]>::try_from(&hash[..]).expect("Hash slice has incorrect length"),
        )
    };

    let deposit_storage_key: alloy_primitives::Uint<256, 4> =
        storage_address_deposit + U256::from(storage_proof.index);
```

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L139-145)
```rust
/// - `storage_proof`: A reference to an `EIP1186StorageProof` containing the key, value, and Merkle proof.
/// - `expected_root_hash`: A 32-byte array representing the expected root hash of the storage Merkle tree.
///
/// # Panics
///
/// - If Borsh deserialization of `storage_proof.proof[0]` fails.
/// - If Merkle proof verification fails.
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L188-204)
```rust
    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L221-229)
```rust
    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```

**File:** core/src/citrea.rs (L247-253)
```rust
    async fn get_storage_proof(
        &self,
        l2_height: u64,
        deposit_index: u32,
    ) -> Result<StorageProof, BridgeError> {
        let ind = deposit_index;
        let tx_index: u32 = ind * 2;
```
