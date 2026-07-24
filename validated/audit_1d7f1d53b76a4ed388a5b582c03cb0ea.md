### Title
Missing `chain_id` in EIP-712 Domain Enables Cross-Network Replay of Withdrawal Authorization Signatures — (File: `core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

The `CLEMENTINE_EIP712_DOMAIN` used to authenticate aggregator-signed withdrawal authorizations is defined without a `chain_id`. Because the domain separator is therefore identical across every EVM-compatible chain, a valid withdrawal authorization signature produced for one Citrea deployment (e.g., testnet) can be replayed verbatim against a different Citrea deployment (e.g., mainnet) and will pass the operator's signature-recovery check, potentially authorizing an unintended BTC payout.

---

### Finding Description

`CLEMENTINE_EIP712_DOMAIN` is declared as:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

No `chain_id`, `verifying_contract`, or `salt` is included. The EIP-712 specification recommends `chain_id` precisely to prevent cross-chain replay; without it the `domainSeparator` hash is the same on every chain that uses this code.

This domain is the sole input to `eip712_signing_hash` for both `OptimisticPayoutMessage` (optimistic payout path) and `OperatorWithdrawalMessage` (normal withdrawal path):

```rust
let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
``` [2](#0-1) 

The recovered address is then compared to `aggregator_verification_address` in the operator's config:

```rust
if address_from_sig != address_in_config {
    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
}
``` [3](#0-2) 

Because the domain is chain-agnostic, a signature that recovers the correct aggregator address on testnet will recover the same address on mainnet — the check passes on both networks for the same raw signature bytes.

The same vulnerability exists on the optimistic payout path in `sign_optimistic_payout`:

```rust
let address_from_sig =
    recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
        deposit_id, input_signature, input_outpoint,
        output_script_pubkey.clone(), output_amount,
        verification_signature,
    )?;
if address_from_sig != address_in_config {
    return Err(BridgeError::InvalidECDSAVerificationSignature);
}
``` [4](#0-3) 

---

### Impact Explanation

If the same aggregator key is configured on both a Citrea mainnet and a Citrea testnet deployment of Clementine (a common operational pattern), an attacker who obtains any legitimately-issued testnet withdrawal authorization signature can submit it to the mainnet operator. The operator's `recover_address_from_ecdsa_signature` call will recover the correct aggregator address (because the domain hash is identical), the address comparison will pass, and the operator will proceed to execute the payout transaction — spending bridge-controlled BTC without a genuine mainnet authorization.

The downstream `operator.withdraw` call validates the `withdrawal_id` against Citrea state and checks that `input_outpoint` matches the registered withdrawal UTXO:

```rust
let withdrawal_utxo = self
    .db
    .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
    .await?;
if withdrawal_utxo != input_utxo.outpoint {
    return Err(...);
}
``` [5](#0-4) 

This check is satisfied whenever the same `withdrawal_id` maps to the same Bitcoin UTXO on the target network — a condition that holds when the same Bitcoin transaction is indexed by both Citrea deployments (both networks can observe the same Bitcoin mainnet chain).

---

### Likelihood Explanation

**Medium.** The preconditions are:
1. The same aggregator ECDSA key is used on two Citrea deployments (standard for testnet/mainnet mirrors).
2. The same Bitcoin UTXO is registered as a withdrawal UTXO on both Citrea networks (possible when both deployments watch the same Bitcoin mainnet).
3. The attacker can observe a testnet authorization signature (e.g., from public RPC logs or by being the withdrawal requester on testnet).

None of these require privileged access or malicious deployment; they reflect normal multi-environment operations.

---

### Recommendation

Bind the domain to the specific Citrea chain by including `chain_id`:

```rust
pub fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass the chain ID from `BridgeConfig` (which already tracks Citrea chain ID for compatibility checks) when constructing the domain at signing and verification time. This ensures a testnet signature cannot satisfy a mainnet domain separator.

---

### Proof of Concept

1. Deploy Clementine on Citrea testnet and Citrea mainnet, both watching Bitcoin mainnet, with the same `aggregator_verification_address` / key.
2. On testnet: user deposits BTC (Bitcoin UTXO `U`), Citrea testnet assigns `withdrawal_id = N` pointing to `U`.
3. Aggregator signs `OperatorWithdrawalMessage { withdrawal_id: N, ..., input_outpoint_txid: U.txid, ... }` using `CLEMENTINE_EIP712_DOMAIN` (no chain_id) → produces signature `S`.
4. On mainnet: the same Bitcoin UTXO `U` is also indexed (same Bitcoin chain), and Citrea mainnet happens to assign `withdrawal_id = N` to `U` (sequential IDs, same deposit ordering).
5. Attacker calls the mainnet operator's `Withdraw` RPC with `withdrawal_id=N`, `input_outpoint=U`, and `verification_signature=S`.
6. `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` recomputes the hash with the same chain-agnostic `CLEMENTINE_EIP712_DOMAIN` → recovers the aggregator address → check passes.
7. `operator.withdraw` validates `U` against Citrea mainnet state → passes → payout transaction is broadcast, spending mainnet bridge BTC without a genuine mainnet authorization. [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** core/src/verifier.rs (L1605-1617)
```rust
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
```

**File:** core/src/operator.rs (L589-596)
```rust
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }
```
