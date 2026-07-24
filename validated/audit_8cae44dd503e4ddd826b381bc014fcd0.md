### Title
Missing `chain_id` in `CLEMENTINE_EIP712_DOMAIN` Enables Cross-Network Replay of Withdrawal Verification Signatures — (`core/src/rpc/ecdsa_verification_sig.rs`)

### Summary

`CLEMENTINE_EIP712_DOMAIN` is defined without a `chain_id` or `verifying_contract` field. Every EIP-712 signing hash produced by `recover_address_from_ecdsa_signature` is therefore identical across all Citrea deployments (mainnet, testnet4, signet, regtest) that share the same aggregator ECDSA key. A valid `verification_signature` captured from one network can be submitted verbatim to a different network's verifiers or operators, bypassing the only aggregator-identity gate that guards optimistic payout and operator withdrawal flows.

### Finding Description

`CLEMENTINE_EIP712_DOMAIN` is declared as a static constant with only `name` and `version`:

```rust
// core/src/rpc/ecdsa_verification_sig.rs  lines 42-45
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

The EIP-712 specification requires `chainId` (and optionally `verifyingContract`) to prevent cross-chain replay. Both are absent here.

This domain is the sole input to `eip712_signing_hash` in `recover_address_from_ecdsa_signature`:

```rust
// line 125
let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
``` [2](#0-1) 

That recovered address is then compared against `aggregator_verification_address` in two places:

1. **Verifier** — `sign_optimistic_payout` (lines 1602–1618): if the recovered address matches the config address, the verifier produces a MuSig2 partial signature that ultimately spends the vault UTXO (the move-tx output holding bridged BTC). [3](#0-2) 

2. **Operator** — `withdraw` RPC handler (lines 210–238): same check gates the operator's payout transaction. [4](#0-3) 

Because the domain carries no `chain_id`, the 65-byte ECDSA signature produced for `(withdrawal_id, input_outpoint, output_script_pubkey, output_amount)` on Citrea testnet4 is byte-for-byte identical to the signature that would be produced for the same tuple on Citrea mainnet. Any party that observes a legitimate aggregator signature on one network can submit it unchanged to the other.

**Concrete replay path:**

1. Aggregator signs a testnet4 optimistic-payout request for `withdrawal_id=N`, `input_outpoint=T:V`, `output_script_pubkey=<attacker_addr>`, `output_amount=A`. The signature is broadcast or logged.
2. The same Bitcoin UTXO `T:V` is a valid withdrawal UTXO on mainnet Citrea (possible when the same Bitcoin node indexes both, or after a Citrea chain fork).
3. Attacker submits the captured signature to mainnet verifiers via `optimistic_payout_sign`. Each verifier calls `recover_address_from_ecdsa_signature` with the same chain-agnostic domain, recovers the same aggregator address, passes the check, and issues a partial MuSig2 signature.
4. Aggregator combines partial signatures into a full MuSig2 signature and broadcasts the payout transaction, spending the mainnet vault UTXO to the attacker's address.

The `OperatorWithdrawalMessage` path (`withdraw` RPC) is identically affected. [5](#0-4) 

### Impact Explanation

A successful replay causes the vault UTXO (the move-tx output containing the full bridge amount of bridged BTC) to be spent to an attacker-controlled address. The loss is equal to the bridge amount for each replayed withdrawal. The `aggregator_verification_address` guard is the only mechanism preventing an unauthorized party from triggering optimistic payouts; bypassing it removes that protection entirely for any deployment that sets this config field. [6](#0-5) 

### Likelihood Explanation

The preconditions are realistic:
- Citrea's own documentation and scripts show `CITREA_CHAIN_ID=5115` as a default, and the same aggregator key is commonly reused across testnet and mainnet deployments during early launch.
- The `aggregator_verification_address` field is explicitly described as active "during some time after launch," meaning the vulnerable code path is exercised in production.
- A Citrea chain fork (analogous to ETH/ETC) would make all pre-fork signatures universally replayable with zero additional preconditions. [7](#0-6) 

### Recommendation

Include `chain_id` (and `verifying_contract` if a bridge contract address is available) in `CLEMENTINE_EIP712_DOMAIN`. Because the domain must be known at signing time, it should be constructed at runtime from the live Citrea chain ID rather than as a compile-time static:

```rust
pub fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass the chain ID (obtained from the Citrea RPC at startup) into every call site that currently uses `CLEMENTINE_EIP712_DOMAIN`. This mirrors the fix applied to the Golom finding: recompute the domain binding on every use rather than caching a chain-agnostic constant.

### Proof of Concept

```
# 1. On Citrea testnet4 (chain_id = 5115-testnet or similar):
#    Aggregator signs OptimisticPayoutMessage for:
#      withdrawal_id = 7
#      input_outpoint = <txid>:<vout>   (same Bitcoin UTXO indexed on both nets)
#      output_script_pubkey = <attacker P2TR>
#      output_amount = 1_000_000_000 sats
#    → produces verification_signature S

# 2. On Citrea mainnet (chain_id = 5115):
#    Attacker submits to aggregator's optimistic_payout RPC:
#      withdrawal_id = 7, same input_outpoint, same output fields, sig = S

# 3. Verifier calls recover_address_from_ecdsa_signature:
#    domain = { name="ClementineVerification", version="1" }   ← no chain_id
#    eip712_hash is identical to testnet hash → recovered address == aggregator address
#    → check passes, verifier signs MuSig2 partial sig

# 4. Aggregator aggregates partial sigs, broadcasts payout tx.
#    Vault UTXO (bridge amount) is sent to attacker's address.
```

The signing hash identity can be verified directly: constructing `OptimisticPayoutMessage` with the same field values and calling `eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN)` on any two Citrea networks produces the same 32-byte digest because the domain contributes only `keccak256("ClementineVerification" || "1")` with no network discriminator. [8](#0-7)

### Citations

**File:** core/src/rpc/ecdsa_verification_sig.rs (L32-39)
```rust
    struct OperatorWithdrawalMessage  {
        uint32 withdrawal_id;
        bytes input_signature;
        bytes32 input_outpoint_txid;
        uint32 input_outpoint_vout;
        bytes output_script_pubkey;
        uint64 output_amount;
    }
```

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

**File:** core/src/rpc/operator.rs (L210-238)
```rust
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
```

**File:** core/src/config/mod.rs (L148-152)
```rust
    /// The ECDSA address of the citrea/aggregator that will sign the withdrawal params
    /// after manual verification of the optimistic payout and operator's withdrawal.
    /// Used for both an extra verification of aggregator's identity and to force citrea
    /// to check withdrawal params manually during some time after launch.
    pub aggregator_verification_address: Option<alloy::primitives::Address>,
```

**File:** scripts/run.sh (L31-31)
```shellscript
export CITREA_CHAIN_ID=${CITREA_CHAIN_ID:=5115}
```
