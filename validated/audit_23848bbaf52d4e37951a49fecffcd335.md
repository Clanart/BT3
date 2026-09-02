## Finding

### Title
No disprove path or profitability check verifies the payout output amount, letting an operator be reimbursed for a zero/underpaid withdrawal - (File: circuits-lib/src/bridge_circuit/mod.rs, circuits-lib/src/bridge_circuit/storage_proof.rs, core/src/verifier.rs, core/src/operator.rs)

### Summary
The bridge circuit and both verifier-side disprove functions only verify that the payout transaction's **input** spends the exact withdrawal UTXO registered on Citrea (txid+vout); none of them ever check the payout transaction's **output value or script_pubkey** against the amount the withdrawer is actually owed. Combined with `Operator::is_profitable`, which places no floor on `out_amount` (and even has a `checked_sub` underflow branch that unconditionally returns `true` when `out_amount < input_amount`), a payout of 0 (or near-0) sats to the withdrawal address can pass every honest-code check and every disprove check, so the operator is reimbursed the full `bridge_amount` for a payout it effectively never funded.

### Finding Description
The claimed binding is: `existence of a valid Disprove witness == (payout amount paid to withdrawer != recorded withdrawal amount)`. Tracing the code shows the right-hand side of this equality is never computed anywhere in the disprove path.

- In `bridge_circuit()` [1](#0-0) , after SPV/light-client verification the circuit only asserts that `user_wd_outpoint`/`vout` (derived from the Citrea storage proof) equal the payout tx's **input** `previous_output.txid`/`vout`. No output value/script_pubkey of `input.payout_spv.transaction` is ever read or asserted.
- `verify_storage_proofs` [2](#0-1)  only decodes and verifies storage keys `UTXOS_STORAGE_INDEX` (txid, vout) and `DEPOSIT_STORAGE_INDEX` (move txid) — there is no storage slot or check for a withdrawal *amount*.
- On the verifier side, `verify_additional_disprove_conditions` [3](#0-2)  only reconstructs and checks block-hash/watchtower-ack commitments, and `verify_disprove_conditions` [4](#0-3)  only checks the Groth16 assertions of the bridge circuit output — since the bridge circuit's own journal never encodes the payout's paid amount, neither disprove path can ever detect an underpayment.
- The only place an output amount is validated against a signature is `Operator::withdraw()` [5](#0-4) , which computes the sighash from whatever `out_amount`/`out_script_pubkey` was supplied to the RPC and checks the withdrawer's own Schnorr signature against it — this only proves the withdrawer authorized *that specific* amount (possibly 0), not that it matches what they are owed.
- `Operator::is_profitable` [6](#0-5)  never enforces `out_amount ≈ bridge_amount`; it only bounds the operator's own net cost, and its `checked_sub` underflow branch treats `out_amount < input_amount` as trivially "profitable" with only a warning log.

Because none of `is_profitable`, `withdraw()`, `bridge_circuit`, `verify_storage_proofs`, `verify_additional_disprove_conditions`, or `verify_disprove_conditions` ever compares the payout's paid amount to the amount the withdrawer is owed, a payout transaction whose output value is 0 (or any value far below the true entitlement) to the correct withdrawal address/UTXO passes every check in the system as long as the withdrawer's own signature covers that specific (low) amount.

### Impact Explanation
An operator (the party that constructs the payout tx, sends the kickoff, and later claims the reimbursement) can be reimbursed the full `bridge_amount` from the move-to-vault UTXO while having paid the withdrawer effectively nothing. This matches the Critical category "an operator reimbursed for a payout it never funded." The bug is systemic (present in `bridge_circuit`, `verify_disprove_conditions`, and `verify_additional_disprove_conditions`), so it is repeatable across every deposit/withdrawal/operator in the protocol, not tied to one specific kickoff.

### Likelihood Explanation
This requires a withdrawer to sign an outgoing payout with an amount they authorize to be paid to themselves (e.g. 0), which is only rational if the withdrawer is colluding with (or is) the operator claiming reimbursement. The withdraw/deposit steps use only unprivileged, public actions (deposit, Citrea `withdraw`, choosing `in_signature`/`out_amount`/`out_script_pubkey`), so no verifier/aggregator/watchtower/key compromise is needed to construct the malicious inputs; the reimbursement itself still requires an operator to broadcast the kickoff based on that payout, since only operators run kickoffs.

### Recommendation
Encode the withdrawer's expected/entitled payout amount (and script pubkey) into the Citrea contract storage at withdrawal-registration time, add a storage-proof slot for it, and have `bridge_circuit` (and the corresponding disprove scripts) assert that the payout transaction's output value/script at `payout_input_index` matches that committed amount. Additionally, fix `Operator::is_profitable` to reject `out_amount` values that are not close to the intended full withdrawal amount rather than only bounding the operator's own cost.

### Proof of Concept
A `cargo test` (outside `core/src/test/**`, e.g. a new circuits-lib unit test / core verifier unit test) would:
1. Build a `BridgeCircuitInput` (or a `DepositData`/`KickoffData` fixture for `verify_disprove_conditions`/`verify_additional_disprove_conditions`) where the payout transaction's input correctly references the registered withdrawal UTXO (so storage-proof and SPV checks pass) but the payout's output value is `0`.
2. Assert `bridge_circuit`/`verify_storage_proofs` do not panic (no amount check exists) — i.e. the left side of the equality is never computed.
3. Call `verify_additional_disprove_conditions` and `verify_disprove_conditions` with this data and assert both return `Ok(None)`, confirming no Disprove transaction is ever generated for a zero-payment payout.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-204)
```rust
    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

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

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L44-132)
```rust
pub fn verify_storage_proofs(
    storage_proof: &StorageProof,
    state_root: [u8; 32],
) -> (WithdrawalOutpointTxid, u32, MoveTxid) {
    let utxo_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_utxo)
            .expect("Failed to deserialize UTXO storage proof");

    let vout_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_vout)
            .expect("Failed to deserialize vout storage proof");

    let deposit_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_deposit_txid)
            .expect("Failed to deserialize deposit storage proof");

    let storage_address: U256 = {
        let mut keccak = Keccak256::new();
        keccak.update(UTXOS_STORAGE_INDEX);
        let hash = keccak.finalize();
        U256::from_be_bytes(
            <[u8; 32]>::try_from(&hash[..]).expect("Hash slice has incorrect length"),
        )
    };

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

    let deposit_storage_key_bytes = deposit_storage_key.to_be_bytes::<32>();

    if deposit_storage_key_bytes != deposit_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid deposit storage key. left: {:?} right: {:?}",
            deposit_storage_key_bytes,
            deposit_storage_proof.key.as_b256().0
        );
    }

    if storage_key_utxo.to_be_bytes() != utxo_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid withdrawal UTXO storage key. left: {:?} right: {:?}",
            storage_key_utxo.to_be_bytes::<32>(),
            utxo_storage_proof.key.as_b256().0
        );
    }

    if storage_key_vout.to_be_bytes() != vout_storage_proof.key.as_b256().0 {
        panic!(
            "Invalid withdrawal vout storage key. left: {:?} right: {:?}",
            storage_key_vout.to_be_bytes::<32>(),
            vout_storage_proof.key.as_b256().0
        );
    }

    storage_verify(&utxo_storage_proof, state_root);

    storage_verify(&deposit_storage_proof, state_root);

    storage_verify(&vout_storage_proof, state_root);

    let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

    // ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
    let vout = u32::from_le_bytes(
        buf[28..32]
            .try_into()
            .expect("Vout value conversion failed"),
    );

    let wd_outpoint = WithdrawalOutpointTxid(utxo_storage_proof.value.to_be_bytes());

    let move_txid = MoveTxid(deposit_storage_proof.value.to_be_bytes());

    (wd_outpoint, vout, move_txid)
```

**File:** core/src/verifier.rs (L2420-2430)
```rust
    async fn verify_additional_disprove_conditions(
        &self,
        deposit_data: &mut DepositData,
        kickoff_data: &KickoffData,
        latest_blockhash: &Witness,
        payout_blockhash: &Witness,
        operator_asserts: &HashMap<usize, Witness>,
        operator_acks: &HashMap<usize, Witness>,
        txhandlers: &BTreeMap<TransactionType, TxHandler>,
        db_cache: &mut ReimburseDbCache<'_>,
    ) -> Result<Option<bitcoin::Witness>, BridgeError> {
```

**File:** core/src/verifier.rs (L2776-2782)
```rust
    async fn verify_disprove_conditions(
        &self,
        deposit_data: &mut DepositData,
        operator_asserts: &HashMap<usize, Witness>,
        db_cache: &mut ReimburseDbCache<'_>,
    ) -> Result<Option<(usize, Vec<Vec<u8>>)>, BridgeError> {
        use bitvm::chunk::api::{NUM_HASH, NUM_PUBS, NUM_U256};
```

**File:** core/src/operator.rs (L503-537)
```rust
    fn is_profitable(
        input_amount: Amount,
        withdrawal_amount: Amount,
        bridge_amount_sats: Amount,
        operator_withdrawal_fee_sats: Amount,
    ) -> bool {
        // Use checked_sub to safely handle potential underflow
        let withdrawal_diff = match withdrawal_amount
            .to_sat()
            .checked_sub(input_amount.to_sat())
        {
            Some(diff) => Amount::from_sat(diff),
            None => {
                // input amount is greater than withdrawal amount, so it's profitable but doesn't make sense
                tracing::warn!(
                    "Some user gave more amount than the withdrawal amount as input for withdrawal"
                );
                return true;
            }
        };

        if withdrawal_diff > bridge_amount_sats {
            return false;
        }

        // Calculate net profit after the withdrawal using checked_sub to prevent panic
        let net_profit = match bridge_amount_sats.checked_sub(withdrawal_diff) {
            Some(profit) => profit,
            None => return false, // If underflow occurs, it's not profitable
        };

        // Net profit must be bigger than withdrawal fee.
        // net profit doesn't take into account the fees, but operator_withdrawal_fee_sats should
        net_profit >= operator_withdrawal_fee_sats
    }
```

**File:** core/src/operator.rs (L620-637)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```
