### Title
Unprivileged user can forge `ReplacementDeposit` deposits with a fabricated `old_move_txid` and get N-of-N verifier signatures - (File: `core/src/verifier.rs`)

### Summary
`Verifier::is_deposit_valid` never checks that `ReplacementDepositData::old_move_txid` corresponds to a real, previously-created `MoveToVault` transaction that was actually spent through the `Multisig::from_security_council` script path. It only verifies that the on-chain deposit output matches the taproot address deterministically derived from the attacker-supplied `old_move_txid`, and that its value equals `bridge_amount`. This lets any unprivileged depositor invent a random 32-byte `old_move_txid`, fund the corresponding `generate_replacement_deposit_address` output themselves, and have all verifiers `deposit_sign`/`deposit_finalize` it as a legitimate replacement deposit.

### Finding Description
Binding that should hold: `ReplacementDepositData::old_move_txid == txid of a TxHandler of TransactionType::MoveToVault that was actually spent by a Multisig::from_security_council witness`.

The address for a replacement deposit is built purely from parameters supplied in the request: [1](#0-0) 

`is_deposit_valid` in `core/src/verifier.rs` recomputes the expected taproot scriptPubKey from `deposit_data.get_deposit_scripts()` (which for a `ReplacementDeposit` embeds the attacker-chosen `old_move_txid` via `ReplacementDepositScript`), compares it against the on-chain output, and checks the amount equals `bridge_amount`: [2](#0-1) 

Nowhere in this function (nor earlier in the same call, lines 541-658) is there a lookup of `old_move_txid` against the database of previously created `MoveToVault` transactions, nor a check that such a transaction was spent by the security-council multisig leaf. `deposit_sign` and `deposit_finalize` both call `is_deposit_valid` as their only gate before generating verifier partial signatures over the new deposit's sighashes: [3](#0-2) [4](#0-3) 

Since the script (and hence the on-chain address) is fully determined by attacker-controlled inputs (`old_move_txid`, the fixed `nofn_xonly_pk`, network, and the config's `security_council`), the attacker can:
1. Pick any random 32-byte value as `old_move_txid` that was never a real `MoveToVault` txid.
2. Compute the corresponding address via `generate_replacement_deposit_address` (exposed by the aggregator CLI, `GetReplacementDepositAddress`) and send exactly `bridge_amount` BTC to it.
3. Submit `NewReplacementDeposit` with this outpoint and fabricated `old_move_txid` to the aggregator.
4. All verifiers run `is_deposit_valid`, which passes every check because the on-chain output matches what the fabricated `old_move_txid` would produce and the amount is correct.
5. Verifiers proceed to `deposit_sign`/`deposit_finalize`, producing full N-of-N signatures over the resulting `MoveToVault` transaction.

The `Multisig::from_security_council` leaf in the address is only a *spending* path for the security council to move an old deposit into this new address; `is_deposit_valid` never confirms that this leaf was actually exercised against a genuine prior deposit before accepting the new deposit as valid.

### Impact Explanation
The verifier code path itself only lets the attacker get a co-signed `MoveToVault` transaction for BTC that the attacker funded themselves at the `generate_replacement_deposit_address` output — this portion does not directly move third-party BTC. The critical/severe consequence described in the question (mint authority violation, a second credited-deposit event) depends on how the Citrea Bridge contract interprets a `MoveToVault` transaction tagged as a "replacement" (e.g., whether it re-credits the EVM account tied to the original `old_move_txid` without independently confirming that a genuine prior deposit and security-council-authorized replacement actually occurred). That crediting logic lives in the Citrea Bridge contract, which is outside this repository, so the "double-credit"/mint-authority-violation outcome cannot be confirmed purely from `core/src/verifier.rs`.

What is confirmed and demonstrable purely from this repo is that `is_deposit_valid` fails to enforce the stated binding: it allows a `ReplacementDeposit` to be validated, signed, and finalized by all N verifiers even when `old_move_txid` names no real prior `MoveToVault` output and was never spent by the security council multisig. This is a genuine control-gap in the intended trust model (only the security council should be able to originate replacement deposits) but the "Critical: mint authority violated" impact is contingent on external, unverifiable Citrea contract behavior.

### Likelihood Explanation
Trivial precondition-wise: any user who can broadcast Bitcoin transactions, compute a taproot address from public parameters (`nofn_xonly_pk`, network, security council config — all obtainable via public aggregator RPCs like `GetNofnAggregatedKey`/`GetReplacementDepositAddress`), and fund it with exactly `bridge_amount` BTC of their own money can trigger this. No verifier, operator, or security-council privileges are required. Cost is exactly one `bridge_amount` payment plus fees, and it is repeatable for arbitrarily many fabricated `old_move_txid` values.

### Recommendation
In `Verifier::is_deposit_valid`, when `deposit_data.deposit.deposit_type` is `DepositType::ReplacementDeposit(ReplacementDepositData { old_move_txid })`, add an explicit check that:
1. `old_move_txid` corresponds to a `TxHandler` of `TransactionType::MoveToVault` known to this verifier's database (i.e., a deposit this verifier itself previously signed/finalized), and
2. that prior `MoveToVault` output has actually been spent on-chain via the `Multisig::from_security_council` leaf of its taproot script (verify the witness script/leaf used in the spending transaction), before accepting the new deposit as valid. Reject with `BridgeError::InvalidDeposit` otherwise.

### Proof of Concept
```
cargo test -p clementine-core --features automation deposit::replacement_deposit_fabricated_old_move_txid
```
Test plan:
1. Set up verifiers/aggregator/security council as in existing replacement-deposit test helpers (`run_single_replacement_deposit` in `core/src/test/common/mod.rs`).
2. Instead of using a real prior `move_txid` returned from a genuine base deposit, generate a random 32-byte `Txid` (`old_move_txid = Txid::from_byte_array(rand::random())`) that was never produced by `create_move_to_vault_txhandler`.
3. Call `generate_replacement_deposit_address(old_move_txid, nofn_xonly_pk, network, security_council)`, fund the resulting address with exactly `bridge_amount`, and mine it.
4. Construct `DepositInfo { deposit_outpoint, deposit_type: DepositType::ReplacementDeposit(ReplacementDepositData { old_move_txid }) }` and call `aggregator.new_deposit` / drive `deposit_sign`/`deposit_finalize`.
5. Assert (binding check): before the call, `old_move_txid` is NOT present in the verifier DB's set of known `MoveToVault` txids and no on-chain transaction spends it via `Multisig::from_security_council`; after the call, assert that `Verifier::is_deposit_valid` returns `Ok(())` (demonstrating the missing check) instead of the expected `Err(BridgeError::InvalidDeposit(_))`.
6. Optionally assert that `deposit_finalize` succeeds and produces valid N-of-N signatures for the resulting `MoveToVault` transaction, confirming the fabricated replacement is fully signed despite the broken binding.

### Citations

**File:** core/src/builder/address.rs (L89-103)
```rust
pub fn generate_replacement_deposit_address(
    old_move_txid: bitcoin::Txid,
    nofn_xonly_pk: XOnlyPublicKey,
    network: bitcoin::Network,
    security_council: SecurityCouncil,
) -> Result<(Address, TaprootSpendInfo), BridgeError> {
    let deposit_script =
        ReplacementDepositScript::new(nofn_xonly_pk, old_move_txid).to_script_buf();

    let security_council_script = Multisig::from_security_council(security_council).to_script_buf();

    let (addr, spend) =
        create_taproot_address(&[deposit_script, security_council_script], None, network);
    Ok((addr, spend))
}
```

**File:** core/src/verifier.rs (L659-705)
```rust
        // check if deposit script in deposit_outpoint is valid
        let deposit_scripts: Vec<ScriptBuf> = deposit_data
            .get_deposit_scripts(self.config.protocol_paramset())?
            .into_iter()
            .map(|s| s.to_script_buf())
            .collect();
        // what the deposit scriptpubkey is in the deposit_outpoint should be according to the deposit data
        let expected_scriptpubkey = create_taproot_address(
            &deposit_scripts,
            None,
            self.config.protocol_paramset().network,
        )
        .0
        .script_pubkey();
        let deposit_outpoint = deposit_data.get_deposit_outpoint();
        let deposit_txid = deposit_outpoint.txid;
        let deposit_tx = self
            .rpc
            .get_tx_of_txid(&deposit_txid)
            .await
            .wrap_err("Deposit tx could not be found on chain")?;
        let deposit_txout_in_chain = deposit_tx
            .output
            .get(deposit_outpoint.vout as usize)
            .ok_or(eyre::eyre!(
                "Deposit vout not found in tx {}, vout: {}",
                deposit_txid,
                deposit_outpoint.vout
            ))?;
        if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount {
            let reason = format!(
                "Deposit amount is not correct, expected {}, got {}",
                self.config.protocol_paramset().bridge_amount,
                deposit_txout_in_chain.value
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        if deposit_txout_in_chain.script_pubkey != expected_scriptpubkey {
            let reason = format!(
                "Deposit script pubkey in deposit outpoint does not match the deposit data, expected {:?}, got {:?}",
                expected_scriptpubkey,
                deposit_txout_in_chain.script_pubkey
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
```

**File:** core/src/verifier.rs (L866-886)
```rust
    pub async fn deposit_sign(
        &self,
        mut deposit_data: DepositData,
        session_id: u128,
        mut agg_nonce_rx: mpsc::Receiver<AggregatedNonce>,
    ) -> Result<mpsc::Receiver<Result<PartialSignature, BridgeError>>, BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(&mut deposit_data).await?;

        // set deposit data to db before starting to sign, ensures that if the deposit data already exists in db, it matches the one
        // given by the aggregator currently. We do not want to sign 2 different deposits for same deposit_outpoint
        self.db
            .insert_deposit_data_if_not_exists(
                None,
                &mut deposit_data,
                self.config.protocol_paramset(),
            )
            .await?;
```

**File:** core/src/verifier.rs (L982-994)
```rust
    pub async fn deposit_finalize(
        &self,
        deposit_data: &mut DepositData,
        session_id: u128,
        mut sig_receiver: mpsc::Receiver<Signature>,
        mut agg_nonce_receiver: mpsc::Receiver<AggregatedNonce>,
        mut operator_sig_receiver: mpsc::Receiver<Signature>,
    ) -> Result<(PartialSignature, PartialSignature), BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(deposit_data).await?;
```
