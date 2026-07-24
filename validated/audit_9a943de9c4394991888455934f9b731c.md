### Title
Missing `chain_id` in `CLEMENTINE_EIP712_DOMAIN` Enables Cross-Deployment Signature Replay for Withdrawal Authorization — (File: `core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

`CLEMENTINE_EIP712_DOMAIN` is a process-wide static constant containing only `name` and `version`, with no `chain_id` or `verifying_contract`. Any ECDSA signature the aggregator produces to authorize an optimistic payout or operator withdrawal is therefore valid on every Clementine deployment that shares the same aggregator key, regardless of which Citrea network or bridge instance it was intended for. The proto comment at `clementine.proto:490-492` explicitly acknowledges cross-message-type replay and prevents it via different struct names, but cross-network/cross-deployment replay through the domain separator is not addressed.

---

### Finding Description

**Root cause — `core/src/rpc/ecdsa_verification_sig.rs:42-45`:**

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
```

`BridgeConfig` carries `citrea_chain_id: u32` (e.g., `5655` for mainnet, `62298` for regtest), but this value is never fed into the domain. The domain hash is therefore identical for every deployment.

**Signing path (aggregator / test helper):**
`core/src/test/sign.rs:33` — `params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN)`

**Verification path 1 — verifier optimistic payout (`core/src/verifier.rs:1605-1613`):**
```rust
recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
    deposit_id, input_signature, input_outpoint,
    output_script_pubkey.clone(), output_amount,
    verification_signature,
)?;
```

**Verification path 2 — operator withdrawal (`core/src/rpc/operator.rs:221-229`):**
```rust
recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
    withdrawal_id, input_signature, input_outpoint,
    output_script_pubkey.clone(), output_amount,
    verification_signature,
)?;
```

Both paths call `params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN)` with the same static domain, so a signature produced for Citrea chain A is cryptographically valid on Citrea chain B.

**Concrete replay scenario:**

1. Clementine is deployed against Citrea mainnet (`chain_id=5655`) and Citrea testnet (`chain_id=62298`), both anchored to Bitcoin mainnet (same UTXO set), both configured with the same `aggregator_verification_address`.
2. The aggregator signs `OperatorWithdrawalMessage{withdrawal_id=N, input_outpoint=X, output_script_pubkey=P, output_amount=A}` for the testnet deployment.
3. An attacker (or a compromised aggregator path) submits the identical `WithdrawParamsWithSig` to the mainnet operator.
4. `recover_address_from_ecdsa_signature` recovers the correct aggregator address because the domain hash is identical; the check at `operator.rs:232` passes.
5. The mainnet operator calls `self.operator.withdraw(...)`, which looks up `withdrawal_id=N` in its own database, finds a matching deposit, and broadcasts the payout transaction spending UTXO `X` on Bitcoin mainnet.

The same replay applies to the verifier's `sign_optimistic_payout` path, causing verifiers to co-sign a MuSig2 partial signature for an optimistic payout that was only authorized for a different deployment.

---

### Impact Explanation

A successful replay causes the operator on the target deployment to spend a Bitcoin UTXO and pay out bridge funds without a legitimate withdrawal request on that deployment. The operator's collateral or the bridge's locked BTC is disbursed to the attacker-controlled `output_script_pubkey`. Because the `aggregator_verification_address` guard is the only application-layer check between the gRPC call and the actual payout transaction broadcast, bypassing it via domain-separator replay directly enables unauthorized fund movement.

---

### Likelihood Explanation

Preconditions that must hold simultaneously:
- `aggregator_verification_address` is configured (it is optional, but the config comment says it is intended for production launch).
- The same aggregator key is used across two deployments on the same Bitcoin network (e.g., Citrea mainnet and testnet both anchored to Bitcoin mainnet, or two bridge versions during migration).
- A matching `withdrawal_id` and `input_outpoint` exist in the target deployment's database.

The third condition is the binding constraint. It is satisfied during bridge migrations or when the same Bitcoin UTXO appears in multiple deployments. Likelihood is **low-medium**: the code defect is unconditional, but exploitation requires a specific operational configuration.

---

### Recommendation

Include `citrea_chain_id` from `BridgeConfig` in the EIP-712 domain at the point where the domain is constructed, rather than using a process-wide static. Because `Eip712Domain` supports a `chain_id` field, the fix is:

```rust
// Build per-config, not as a static
fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass `config.citrea_chain_id as u64` wherever `CLEMENTINE_EIP712_DOMAIN` is currently referenced in `recover_address_from_ecdsa_signature`, `sign_optimistic_payout`, `operator.rs::withdraw`, and the test helper `sign_withdrawal_verification_signature`.

---

### Proof of Concept

```
Deployment A: citrea_chain_id = 5655  (mainnet)
Deployment B: citrea_chain_id = 62298 (testnet)
Both: aggregator_verification_address = 0xAGGREGATOR, same Bitcoin node

1. Aggregator signs for deployment B:
   msg = OperatorWithdrawalMessage { withdrawal_id: 7, input_outpoint: "abc...01",
                                     output_script_pubkey: ATTACKER_SCRIPT, output_amount: 1_000_000_000 }
   domain = { name: "ClementineVerification", version: "1" }   // no chain_id
   sig_B = ECDSA_sign(keccak256(domain_separator || type_hash(msg)))

2. Attacker submits sig_B to deployment A's operator gRPC `withdraw` endpoint
   with the same WithdrawParamsWithSig.

3. operator.rs:221-229 calls recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>
   using the same static CLEMENTINE_EIP712_DOMAIN → recovers 0xAGGREGATOR → check passes.

4. operator.rs:241-250 calls self.operator.withdraw(7, ..., "abc...01", ATTACKER_SCRIPT, 1_000_000_000)
   → payout tx broadcast on Bitcoin mainnet, bridge BTC sent to attacker.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/rpc/ecdsa_verification_sig.rs (L42-45)
```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
```

**File:** core/src/rpc/ecdsa_verification_sig.rs (L109-130)
```rust
pub fn recover_address_from_ecdsa_signature<M: WithdrawalMessage + alloy_sol_types::SolStruct>(
    deposit_id: u32,
    input_signature: taproot::Signature,
    input_outpoint: OutPoint,
    output_script_pubkey: ScriptBuf,
    output_amount: Amount,
    signature: PrimitiveSignature,
) -> Result<alloy::primitives::Address, BridgeError> {
    let params = M::new(
        deposit_id,
        input_signature,
        input_outpoint,
        output_script_pubkey,
        output_amount,
    );

    let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);

    let address = signature
        .recover_address_from_prehash(&eip712_hash)
        .wrap_err("Invalid signature")?;
    Ok(address)
```

**File:** core/src/rpc/operator.rs (L209-239)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
            let verification_signature = params
                .verification_signature
                .map(|sig| {
                    PrimitiveSignature::from_str(&sig).map_err(|e| {
                        Status::invalid_argument(format!("Invalid verification signature: {e}"))
                    })
                })
                .transpose()?;
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
                        withdrawal_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
            }
        }
```

**File:** core/src/verifier.rs (L1601-1623)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.config.aggregator_verification_address {
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
                        deposit_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature);
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing);
            }
        }
```

**File:** core/src/config/mod.rs (L148-152)
```rust
    /// The ECDSA address of the citrea/aggregator that will sign the withdrawal params
    /// after manual verification of the optimistic payout and operator's withdrawal.
    /// Used for both an extra verification of aggregator's identity and to force citrea
    /// to check withdrawal params manually during some time after launch.
    pub aggregator_verification_address: Option<alloy::primitives::Address>,
```

**File:** core/src/rpc/clementine.proto (L485-494)
```text
message WithdrawParamsWithSig {
  WithdrawParams withdrawal = 1;
  // An ECDSA signature (of citrea/aggregator) over the withdrawal params
  // to authenticate the withdrawal params. This will be signed manually by
  // citrea after manual verification of the optimistic payout. This message
  // contains same data as the one in Optimistic Payout signature, but with a
  // different message name, so that the same signature can't be used for both
  // optimistic payout and normal withdrawal.
  optional string verification_signature = 2;
}
```
