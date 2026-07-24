### Title
EIP-712 Domain Missing `chain_id` Allows Cross-Network Replay of Withdrawal Authorization Signatures — (`core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

`CLEMENTINE_EIP712_DOMAIN` is constructed without a `chain_id` or `verifying_contract` field. Every `verification_signature` that authorizes an optimistic payout or operator withdrawal is therefore valid on every Citrea network simultaneously. A signature produced for a testnet withdrawal can be replayed verbatim against a mainnet operator or verifier, causing an unauthorized payout of bridged BTC.

---

### Finding Description

`core/src/rpc/ecdsa_verification_sig.rs` defines the single static EIP-712 domain used for both withdrawal message types:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

The `alloy_sol_types::eip712_domain!` macro accepts optional `chain_id` and `verifying_contract` fields; neither is present here. The `citrea_chain_id` value (e.g. `62298` for regtest, `5655` in `.env.example`) is stored in `BridgeConfig` and is even exchanged during the compatibility handshake, but it is never fed into the domain separator. [2](#0-1) 

The domain is consumed in `recover_address_from_ecdsa_signature`, which is the sole verification gate for both flows:

```rust
let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
let address = signature.recover_address_from_prehash(&eip712_hash)...;
``` [3](#0-2) 

**Operator withdrawal path** — `operator.withdraw` calls `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` and compares the recovered address to `aggregator_verification_address` in config: [4](#0-3) 

**Optimistic payout path** — `verifier.sign_optimistic_payout` calls `recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>` under the same check: [5](#0-4) 

Because the domain hash is identical across all Citrea deployments, the ECDSA signature over `(withdrawal_id, input_signature, input_outpoint_txid, input_outpoint_vout, output_script_pubkey, output_amount)` is equally valid on mainnet, testnet4, signet, and regtest.

---

### Impact Explanation

If the same aggregator key is configured across two Citrea deployments (a common operational pattern during staging/migration), an attacker who observes or obtains a legitimately-issued `verification_signature` on the lower-value network can submit it to the higher-value network's operator or verifier RPC. The operator will execute a payout transaction spending the bridge vault UTXO, or the verifier will contribute a MuSig2 partial signature toward an optimistic payout, for a withdrawal that was never authorized on that network. The result is unauthorized disbursement of bridged BTC from the bridge vault.

The `withdrawal_id` is a sequential integer that collides across deployments; the `input_outpoint` is a Bitcoin UTXO that can be the same if the same Bitcoin node is shared (regtest/signet scenarios) or if a UTXO is deliberately constructed to match. The `output_script_pubkey` and `output_amount` are attacker-controlled inputs to the RPC call, so the attacker can craft them to match any existing authorized signature.

---

### Likelihood Explanation

The precondition is that the same `aggregator_verification_address` key is used on more than one Citrea network. This is realistic during testnet-to-mainnet migration, in multi-environment CI pipelines, and in any deployment where operators share a single signing key. The `CLEMENTINE_EIP712_DOMAIN` is a compile-time constant, so no configuration change can mitigate the issue without a code fix.

---

### Recommendation

Include `chain_id` (and optionally `verifying_contract`) in the EIP-712 domain:

```rust
pub fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass `config.citrea_chain_id as u64` wherever `CLEMENTINE_EIP712_DOMAIN` is currently used — in `recover_address_from_ecdsa_signature`, in the operator RPC handler, in the verifier's `sign_optimistic_payout`, and in the test signing helper `sign_withdrawal_verification_signature`. [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. Aggregator signs an `OperatorWithdrawalMessage` on **testnet** Citrea (chain_id=62298) for `withdrawal_id=1`, `input_outpoint=<testnet_utxo>`, `output_amount=1_000_000_000 sat`, `output_script_pubkey=<attacker_p2wpkh>`. The resulting `verification_signature` is a standard 65-byte ECDSA signature.

2. Because `CLEMENTINE_EIP712_DOMAIN` contains only `name` and `version`, the EIP-712 hash is **identical** on mainnet (chain_id=5655) for the same struct fields.

3. Attacker calls the mainnet operator's `withdraw` gRPC endpoint with the same `WithdrawParamsWithSig`, substituting the testnet `verification_signature`.

4. `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` recovers the aggregator's address correctly (same hash, same signature), the address matches `aggregator_verification_address` in config, and the operator proceeds to build and broadcast the payout transaction, spending the mainnet bridge vault UTXO to the attacker's address. [8](#0-7) [1](#0-0)

### Citations

**File:** core/src/rpc/ecdsa_verification_sig.rs (L42-45)
```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
```

**File:** core/src/rpc/ecdsa_verification_sig.rs (L109-131)
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
}
```

**File:** core/src/config/mod.rs (L85-87)
```rust
    /// Citrea's EVM Chain ID.
    pub citrea_chain_id: u32,
    /// Timeout in seconds for Citrea RPC calls.
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

**File:** core/src/verifier.rs (L1601-1618)
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
```

**File:** core/src/test/sign.rs (L33-37)
```rust
    let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);

    let signature = signing_key
        .sign_prehash_recoverable(eip712_hash.as_slice())
        .unwrap();
```
