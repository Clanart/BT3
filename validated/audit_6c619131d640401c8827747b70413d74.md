### Title
Payout OP_RETURN malleability (SINGLE|ANYONECANPAY reuse) lets an attacker force `operator_xonly_pk = None`, causing the genuine funding operator's kickoff to be auto-flagged malicious - (File: core/src/verifier.rs)

### Summary
`Verifier::is_kickoff_malicious` treats a `None` payer operator xonly pk as automatic proof of malice, but the payer identity is derived only from an unsigned, appendable OP_RETURN output rather than from the signature that actually authorizes spending the operator's collateral input. An attacker who copies the honest operator's signed input 0 / output 0 and appends a garbled OP_RETURN can get that malformed transaction mined first, permanently corrupting the on-chain payer record for the withdrawal.

### Finding Description
The claimed binding is: `payout_payer_operator_xonly_pk` stored for withdrawal `idx` (as written by `update_payout_txs_and_payer_operator_xonly_pk`, [1](#0-0) ) should equal the xonly pubkey of the operator whose collateral actually funds output 0 of the confirmed payout transaction for that withdrawal.

`Verifier::is_kickoff_malicious` reads this value back via `get_payout_info_from_move_txid` and, if it is `None`, unconditionally treats the corresponding kickoff as malicious: [2](#0-1) 

The payer identity is populated in `update_finalized_payouts` by parsing an OP_RETURN output with `XOnlyPublicKey::from_slice(parse_op_return_data(...))`, which returns `None` on any parse failure (wrong length, invalid curve point, etc.). Because the payout's authorizing signature only covers input 0 and output 0 (the withdrawal payout), any third party can take the fully-signed input 0 + output 0 pair from the honest operator's broadcast-ready transaction and swap in a different, garbage OP_RETURN payload without invalidating that signature. Nothing in the traced write path (`update_payout_txs_and_payer_operator_xonly_pk`) or the confirmation logic re-derives payer identity from the actual UTXO that funded input 0 - it trusts the unsigned OP_RETURN bytes alone.

Exploit flow:
1. Operator A signs a payout tx spending its collateral for withdrawal `i`, output 0 pays the withdrawal recipient, and A appends an OP_RETURN containing its own xonly pubkey.
2. Before A's transaction is mined, the attacker constructs a transaction reusing A's signed input 0 and output 0, but replaces the OP_RETURN with a 40-byte non-parseable blob, and gets it mined first (a normal fee race, no privileged access needed).
3. During sync, `update_finalized_payouts` fails to parse the OP_RETURN, so `operator_xonly_pk = None`, which is persisted via `update_payout_txs_and_payer_operator_xonly_pk` as `payout_payer_operator_xonly_pk = NULL`.
4. When operator A later performs its kickoff for this withdrawal (which it legitimately funded), `is_kickoff_malicious` finds `operator_xonly_pk_opt = None` at line 1882 and returns `true` regardless of any other check, and regardless of the fact that A's own signature is embedded in the confirmed payout's input 0.
5. This drives the honest operator into a Challenge/Disprove path, burning its collateral.

None of the audited guard functions intervene here: `SECP.verify_schnorr` only validates that the *existing* signature over input 0/output 0 is valid, it says nothing about the OP_RETURN, which is outside the signed message; `is_deposit_valid` and `verify_storage_proofs` operate on the deposit/withdrawal-utxo binding, not on payer attribution; `is_kickoff_malicious`'s own only defense against a bad payer field is to declare malice, which is precisely the exploited behavior.

### Impact Explanation
The honest operator that genuinely funded the withdrawal (its signature is present and valid in the confirmed on-chain payout input) gets `is_kickoff_malicious` return `true`, triggering Challenge and ultimately Disprove, which burns that operator's collateral - matching the Critical category "an honest operator's collateral burned." The attack is repeatable per withdrawal/operator subject only to winning a mempool fee race against the honest operator's own broadcast, so the blast radius scales with the number of payouts processed while an attacker chooses to grief.

### Likelihood Explanation
The attacker needs no privileged role: only the ability to observe an operator's about-to-be-broadcast payout transaction (mempool visibility) and outbid it in fees, which is within the stated attacker capabilities (broadcast Bitcoin transactions, pay fees, craft arbitrary scripts/OP_RETURNs). The cost is one transaction fee per griefed withdrawal, and success only requires the malleated variant to confirm before or instead of the honest one - a realistic mempool race, not a hash-power or consensus attack.

### Recommendation
Do not fall back to "assume malicious" purely because the OP_RETURN failed to parse. Instead, if `operator_xonly_pk_opt` is `None`, verifiers should independently derive the true payer from the actual spent UTXO(s) of input 0 (e.g., by checking whether that outpoint belongs to a known operator's collateral/round transaction graph) before concluding malice, or reject/ignore malleated confirmations that don't match any registered operator's expected payout template, allowing the honest operator's original (well-formed) transaction to be the one credited even if a malleated variant is seen first.

### Proof of Concept
```rust
// core/src/test/... (illustrative outline)
#[tokio::test]
async fn test_op_return_malleation_falsely_flags_honest_operator_malicious() {
    // 1. Set up regtest with verifier + operator A with a real deposit/withdrawal.
    // 2. Have operator A construct and sign its payout tx (input 0 = A's collateral,
    //    output 0 = withdrawal payout, OP_RETURN = A's xonly pk), sighash SINGLE|ANYONECANPAY.
    // 3. Before broadcasting A's tx, attacker copies input 0 + output 0, appends a
    //    40-byte non-32-byte garbage OP_RETURN, and mines this variant first.
    // 4. Run citrea sync / update_finalized_payouts; assert DB:
    //    let (payer_pk, ..) = db.get_payout_info_from_move_txid(..., move_txid).await.unwrap().unwrap();
    //    assert_eq!(payer_pk, None); // binding broken: should be Some(A's pk)
    // 5. Operator A performs its kickoff for the withdrawal.
    // 6. assert!(verifier.is_kickoff_malicious(kickoff_witness, &mut deposit_data, kickoff_data, dbtx).await.unwrap());
    //    -> true, despite A having genuinely funded output 0's input.
}
```

### Citations

**File:** core/src/database/verifier.rs (L199-251)
```rust
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;

        Ok(())
    }
```

**File:** core/src/verifier.rs (L1871-1885)
```rust
        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
```
