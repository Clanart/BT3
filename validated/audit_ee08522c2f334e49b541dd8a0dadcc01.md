### Title
Security council configuration leaked via `InvalidDeposit` error message oracle - (File: core/src/verifier.rs)

### Summary
`Verifier::is_deposit_valid` compares the attacker-supplied `deposit_data.security_council` against `self.config.security_council` and, on mismatch, embeds the verifier's real, configured security council (`pks` and `threshold`) verbatim into the `BridgeError::InvalidDeposit` reason string. Since this check runs on every `deposit_sign` call reachable through `Aggregator::new_deposit`/`Verifier::deposit_sign`, an unprivileged depositor can submit a single deposit request with a dummy `SecurityCouncil` and read the real value back from the error text.

### Finding Description
The binding intended is: `deposit_data.security_council == self.config.security_council` should only gate deposit acceptance, not reveal `self.config.security_council`'s contents to a caller who does not already know them. Instead, at [1](#0-0)  the check builds the error message with `format!(... expected {:?}, got {:?}, self.config.security_council, deposit_data.security_council)` and returns it as `BridgeError::InvalidDeposit(reason)`, which propagates to the gRPC caller as a `Status` message.

The `SecurityCouncil` struct holds `pks: Vec<XOnlyPublicKey>` and `threshold: u32` [2](#0-1) , and this configuration is only otherwise committed on-chain inside a `Multisig` script used exclusively for *replacement* deposits [3](#0-2)  and [4](#0-3) . For a normal base deposit, the security council keys are not part of the deposit script at all — they are only needed if/when the security council must later unlock a replacement deposit (e.g., in response to a discovered bug). Thus, prior to any replacement-deposit event, the security council's exact public keys/threshold are a protocol secret that the design intends to keep undisclosed. An attacker with no privileges beyond depositing can send a `DepositParams` with an arbitrary/dummy `SecurityCouncil{pks, threshold}` through the aggregator's `new_deposit` flow, which fans out to each verifier's `deposit_sign`, triggering `is_deposit_valid`, and the very first mismatched attempt returns the real configured value in the error text.

No existing guard in the reachable path filters or redacts this message: the check is purely an equality test with no rate limiting or authentication gate on `deposit_sign` under the stated precondition (unauthenticated aggregator gRPC), and none of the listed protections (`SECP.verify_schnorr`, `only_aggregator_and_self`, `SPV::verify`, etc.) apply to this code path since it fails before any signing occurs.

### Impact Explanation
No BTC moves and no bridge UTXO is touched by this bug alone — the impact is confined to premature disclosure of the security council public keys and threshold, a protocol commitment intended to remain undisclosed until a replacement deposit is actually needed. This matches the "High - premature disclosure of a protocol commitment" category. It is repeatable at will (a single request suffices; no need to iterate through combinations since the real value is echoed in full on the first mismatch) and applies uniformly to any deposit attempt on any verifier in the deployment, since every verifier's local config holds the same security council value.

### Likelihood Explanation
Under the stated precondition — verifier/aggregator gRPC reachable by an unprivileged depositor without enforced client TLS/authentication — the attack requires only constructing one `DepositParams` with a syntactically valid but arbitrary `SecurityCouncil` and issuing `new_deposit`/`deposit_sign`. It costs no BTC or fees (it fails before any signing or transaction broadcast), and is deterministic and immediately successful on the first call, making it fully repeatable and low-cost.

### Recommendation
Do not include `self.config.security_council` (or any locally-configured secret) in the text of `BridgeError::InvalidDeposit`. Return a generic error (e.g., "security council mismatch") without embedding either side's actual key material/threshold, and log the detailed diagnostic only at the verifier's local `tracing::error!` (already present) rather than surfacing it to the RPC caller via `Status`.

### Proof of Concept
```rust
// core/src/verifier.rs (new test, not touching out-of-scope test dirs)
#[tokio::test]
async fn test_security_council_leak_via_error_message() {
    // Build a verifier with a known, "secret" SecurityCouncil in its config.
    let real_council = SecurityCouncil { pks: vec![/* real xonly pks */], threshold: 2 };
    let verifier = build_test_verifier_with_council(real_council.clone()); // config.security_council = real_council

    // Attacker-controlled deposit_data with a dummy/mismatched council.
    let mut deposit_data = build_dummy_deposit_data_with_council(SecurityCouncil {
        pks: vec![/* attacker dummy pk */],
        threshold: 1,
    });

    let err = verifier.is_deposit_valid(&mut deposit_data).await.unwrap_err();
    let msg = format!("{err}");

    // Assert the real config's security council leaks verbatim in the error text.
    for pk in &real_council.pks {
        assert!(msg.contains(&hex::encode(pk.serialize())));
    }
    assert!(msg.contains(&real_council.threshold.to_string()));
}
```
This demonstrates that `self.config.security_council` (the value meant to stay undisclosed until a replacement deposit is required) is returned verbatim to an unprivileged caller through `BridgeError::InvalidDeposit`, with no mainnet or live Citrea dependency required.

### Citations

**File:** core/src/verifier.rs (L541-551)
```rust
    async fn is_deposit_valid(&self, deposit_data: &mut DepositData) -> Result<(), BridgeError> {
        // check if security council is the same as in our config
        if deposit_data.security_council != self.config.security_council {
            let reason = format!(
                "Security council in deposit is not the same as in the config, expected {:?}, got {:?}",
                self.config.security_council,
                deposit_data.security_council
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
```

**File:** core/src/deposit.rs (L206-218)
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
        }
```

**File:** core/src/deposit.rs (L253-257)
```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecurityCouncil {
    pub pks: Vec<XOnlyPublicKey>,
    pub threshold: u32,
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
