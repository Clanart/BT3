### Title
`Verifier::is_deposit_valid` accepts `ReplacementDeposit` with an unverified `old_move_txid`, granting N-of-N signatures for a fabricated replacement lineage - ([File: core/src/verifier.rs])

### Summary
`Verifier::is_deposit_valid` only checks that a `ReplacementDeposit`'s on-chain output script matches the taproot script derived from the attacker-supplied `old_move_txid`, the current `nofn_xonly_pk`, and the (public) `security_council` config; it never fetches or validates `old_move_txid` on-chain, and never checks that the deposit's funding transaction actually spent that txid's move-to-vault output via the `Multisig::from_security_council` path. An unprivileged depositor can therefore self-fund a fresh outpoint at `generate_replacement_deposit_address(fake_old_move_txid, nofn_xonly_pk, network, security_council)` and obtain full verifier cooperation (nonces, partial signatures) as if it were a legitimate security-council-authorized replacement.

### Finding Description
The invariant the bridge is supposed to enforce is:
`ReplacementDepositData.old_move_txid == txid of a real prior move-to-vault transaction whose output (CheckSig(old_nofn_xonly_pk) OR Multisig(security_council)) was actually spent via the security-council multisig branch to fund the current deposit_outpoint.`

`get_deposit_scripts` (`core/src/deposit.rs:206-217`) builds the expected taproot leaves purely from client-controlled data: [1](#0-0) 
```
DepositType::ReplacementDeposit(replacement_deposit_data) => {
    let deposit_script = Arc::new(ReplacementDepositScript::new(nofn_xonly_pk, replacement_deposit_data.old_move_txid));
    let security_council_script = Arc::new(Multisig::from_security_council(self.security_council.clone()));
    Ok(vec![deposit_script, security_council_script])
}
```
`old_move_txid` is only ever embedded as opaque bytes in the script (`ReplacementDepositScript::to_script_buf`, `core/src/builder/script.rs:526-538`) and `nofn_xonly_pk`/`security_council` are both public. Anyone can therefore compute the exact same taproot address via `generate_replacement_deposit_address` (`core/src/builder/address.rs:89-103`) for any `old_move_txid` value they choose, including one they invented or one that was never spent by the security council.

`Verifier::is_deposit_valid` (`core/src/verifier.rs:541-731`) performs these checks, none of which touch `old_move_txid`'s chain history: [2](#0-1) 
```
let deposit_scripts: Vec<ScriptBuf> = deposit_data.get_deposit_scripts(...)?...;
let expected_scriptpubkey = create_taproot_address(&deposit_scripts, None, ...).0.script_pubkey();
``` [3](#0-2) 
```
if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount { ... }
if deposit_txout_in_chain.script_pubkey != expected_scriptpubkey { ... }
```
followed only by a `start_height` block check (`core/src/verifier.rs:706-731`). There is no lookup of `old_move_txid` via `self.rpc`, no check that it is a known move-to-vault txid in the verifier's DB, and no check that the transaction funding `deposit_outpoint` spends `old_move_txid`'s vault output through the `Multisig::from_security_council` script path (the path used in `create_replacement_deposit_txhandler`, `core/src/builder/transaction/mod.rs:404-446`, and only exercised in tests via `sign_replacement_deposit_tx_with_sec_council`, `core/src/test/common/mod.rs:695-747`).

Exploit flow:
1. Attacker picks an arbitrary `old_move_txid` (e.g. a nonexistent txid, or a real move-to-vault txid that was never actually consumed by the security council).
2. Attacker computes the replacement-deposit taproot address using only public data (`nofn_xonly_pk`, `security_council`).
3. Attacker funds a brand-new outpoint at that address with their own BTC equal to `bridge_amount` (a normal Bitcoin transaction anyone can broadcast).
4. Attacker calls the aggregator's `new_deposit`/`deposit_sign`/`deposit_finalize` gRPC flow with `DepositType::ReplacementDeposit{ old_move_txid }` and this outpoint.
5. `is_deposit_valid` passes (script/amount/height checks all match by construction), so verifiers proceed to generate nonces and emit N-of-N partial signatures over the presigned transaction graph (move-to-vault, kickoffs, etc.) for this deposit — exactly as they would for a genuine security-council-authorized replacement.

Existing guards (`security_council` equality, operator set checks, script/amount/height checks) do not cover this gap because they all validate values the attacker fully controls (the deposit data itself and their own self-funded UTXO), not the claimed historical relationship to `old_move_txid`.

### Impact Explanation
The attacker obtains valid N-of-N verifier partial signatures for a deposit that falsely claims security-council replacement lineage — this matches the explicitly listed Critical category "N-of-N partial signatures for an unauthorised spend". It undermines the core guarantee that a `ReplacementDeposit`'s presigned move-to-vault authority is only ever granted when a security-council quorum genuinely authorized replacing a prior vault output; instead, any unprivileged depositor can obtain the identical signing cooperation for a self-funded, fictitious "replacement". This is repeatable per deposit and does not depend on any specific operator, since it is purely a gap in `Verifier::is_deposit_valid` shared by all verifiers.

### Likelihood Explanation
No special privileges are required: `nofn_xonly_pk` and `security_council` are public configuration, `old_move_txid` is entirely attacker-chosen, and funding the outpoint only costs the `bridge_amount` (the attacker's own money) plus fees. The gRPC `new_deposit`/`deposit_sign` endpoints are reachable by any depositor. The only precondition is that the deposit transaction reaches `start_height`, which is trivial on any live/regtest deployment.

### Recommendation
In `Verifier::is_deposit_valid` (`core/src/verifier.rs`), for `DepositType::ReplacementDeposit`, additionally: fetch `old_move_txid` on-chain and confirm it is a previously recorded move-to-vault transaction in the verifier's DB (e.g., via `db.get_move_to_vault_txid_from_citrea_deposit`/equivalent lookup keyed by deposit id), and verify that the transaction funding the current `deposit_outpoint` actually spends that recorded move-to-vault output's specific vout through the `Multisig::from_security_council` script-path witness (checking the input's `previous_output` and that the spending witness used the multisig leaf, not just that the output script matches).

### Proof of Concept
```rust
// cargo test in core, feature = "automation"
#[tokio::test(flavor = "multi_thread")]
async fn test_replacement_deposit_with_fake_old_move_txid_is_accepted() {
    let mut config = create_test_config_with_thread_name().await;
    let WithProcessCleanup(_, ref rpc, _, _) = create_regtest_rpc(&mut config).await;
    let actors = create_actors::<MockCitreaClient>(&config).await;

    let nofn_xonly_pk = actors.get_nofn_aggregated_xonly_pk().unwrap();
    // Binding LHS: fake old_move_txid never existed / never was spent by security council
    let fake_old_move_txid = Txid::from_byte_array([0x42; 32]);

    // Build the replacement-deposit taproot address purely from public data
    let (addr, _spend_info) = generate_replacement_deposit_address(
        fake_old_move_txid,
        nofn_xonly_pk,
        config.protocol_paramset().network,
        config.security_council.clone(),
    ).unwrap();

    // Attacker self-funds a fresh outpoint at this address (own BTC, no security-council spend)
    let deposit_outpoint = rpc.send_to_address(&addr, config.protocol_paramset().bridge_amount).await.unwrap();
    rpc.mine_blocks(1).await.unwrap();

    let mut aggregator = actors.get_aggregator();
    aggregator.setup(Request::new(Empty {})).await.unwrap();

    let deposit_info = DepositInfo {
        deposit_outpoint,
        deposit_type: DepositType::ReplacementDeposit(ReplacementDepositData { old_move_txid: fake_old_move_txid }),
    };

    // RHS of binding: old_move_txid never existed as a spent vault output -> should be rejected, but is accepted
    let result = aggregator.new_deposit(Into::<Deposit>::into(deposit_info)).await;
    assert!(result.is_ok(), "verifiers signed a replacement deposit with a fabricated old_move_txid that was never a real, security-council-spent move-to-vault output");
}
```
Assertion: `is_deposit_valid` (and therefore `deposit_sign`/`deposit_finalize`) must reject when `old_move_txid` is not a real, previously recorded move-to-vault txid whose output was spent via `Multisig::from_security_council`; the PoC demonstrates it currently returns `Ok`, confirming the missing lineage check.

### Citations

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

**File:** core/src/verifier.rs (L659-672)
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
```

**File:** core/src/verifier.rs (L687-705)
```rust
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
