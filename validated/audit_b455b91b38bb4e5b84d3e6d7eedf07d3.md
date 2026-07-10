### Title
Cross-Chain Signature Replay on `deploy_token` via Missing Chain Identifier in `MetadataPayload` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `MetadataPayload` structure signed by the NEAR MPC for `deploy_token` does not include a destination chain identifier. Because the Borsh-encoded payload is byte-for-byte identical across all bridge deployments (EVM chains, Starknet, Solana), a single MPC signature obtained from a legitimate `deploy_token` call on one chain can be replayed verbatim on any other chain to deploy the same token without NEAR's authorization for that chain.

---

### Finding Description

**Signed payload structure — no chain binding**

On EVM (`OmniBridge.sol::deployToken`), the message hashed and verified is:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),  // 0x01
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [1](#0-0) 

On Starknet (`bridge_types.cairo::MetadataPayloadImpl::to_borsh`), the signed bytes are:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into()); // 0x01
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
}
``` [2](#0-1) 

On Solana (`deploy_token.rs::DeployTokenPayload::serialize_for_near`):

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?; // 0x01
    self.serialize(&mut writer)?; // token, name, symbol, decimals
}
``` [3](#0-2) 

The NEAR-side `MetadataPayload` struct is equally chain-agnostic:

```rust
pub struct MetadataPayload {
    pub prefix: PayloadType,
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
``` [4](#0-3) 

**Contrast with `TransferMessagePayload`**: `finTransfer` / `fin_transfer` correctly embeds `omniBridgeChainId` twice (for token chain and recipient chain) in the signed Borsh blob, binding the signature to a specific destination chain:

```solidity
bytes1(omniBridgeChainId),   // token chain
...
bytes1(omniBridgeChainId),   // recipient chain
``` [5](#0-4) 

`MetadataPayload` has no equivalent binding. The Borsh prefix byte (`0x01` for `Metadata`) and all field encodings are identical across EVM, Starknet, and Solana. The keccak256 of the Borsh blob is therefore identical on every chain for the same token metadata, so the MPC signature is universally valid.

---

### Impact Explanation

An attacker who observes a valid `deploy_token` transaction on chain A (e.g., Ethereum) can:

1. Extract the `signatureData` and `MetadataPayload` from the on-chain calldata.
2. Submit the identical `(signatureData, payload)` to `deployToken` on chain B (e.g., Arbitrum, Base, BNB, Polygon, HyperEVM, Abstract, Starknet, Solana) — all of which share the same `nearBridgeDerivedAddress` signer.
3. The signature passes verification on chain B because the signed bytes are identical.
4. The token is deployed on chain B and registered in that chain's bridge token mapping.
5. The attacker can then submit a proof of the chain-B `DeployToken` event to the NEAR bridge, causing NEAR to register the token address for chain B — without NEAR ever having authorized deployment on chain B.

**Concrete harms**:
- **Unauthorized token deployment**: A token intended only for Ethereum is force-deployed on Arbitrum, Starknet, Solana, etc., without NEAR's per-chain authorization.
- **Blocking legitimate deployment**: Once deployed via replay, `ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED` prevents the legitimate relayer from deploying the token on that chain through the official flow.
- **Registry corruption**: The NEAR bridge's `token_id_to_address` mapping for the target chain is populated with an address that was never officially authorized, potentially causing the NEAR bridge to sign transfer messages for that chain using the attacker-triggered token address.

This matches the allowed HIGH impact: *"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, **token deployment**, or message execution."*

---

### Likelihood Explanation

- **No privileged access required**: The attacker only needs to observe a public `deploy_token` transaction on any chain (calldata is public).
- **Fully deterministic**: The replay works on every chain that shares the same `nearBridgeDerivedAddress` and uses the same Borsh encoding — which is all of them by design.
- **Low cost**: Submitting a transaction on a cheap EVM L2 or Starknet costs negligible gas.
- **Timing**: The window is open from the moment the first legitimate `deploy_token` is broadcast on any chain.

---

### Recommendation

Add the destination chain identifier to the `MetadataPayload` Borsh encoding before it is hashed and signed by the MPC, mirroring the pattern already used in `TransferMessagePayload`. On each chain, the chain-specific constant (`omniBridgeChainId`, `omni_bridge_chain_id`, `SOLANA_OMNI_BRIDGE_CHAIN_ID`) must be prepended or appended to the signed blob:

```solidity
// EVM fix
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // <-- add destination chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

The NEAR bridge's `log_metadata_callback` must include the target chain ID when constructing the `MetadataPayload` sent to the MPC signer, and each destination chain's `deploy_token` must verify the embedded chain ID matches its own.

---

### Proof of Concept

1. NEAR MPC signs `MetadataPayload { prefix: 0x01, token: "usdc.near", name: "USD Coin", symbol: "USDC", decimals: 6 }` for Ethereum deployment. Borsh bytes: `01 | 09000000 75736463 2e6e656172 | ...`.

2. Relayer calls `OmniBridge.deployToken(sig, payload)` on Ethereum. Transaction is public; attacker extracts `sig`.

3. Attacker calls `OmniBridge.deployToken(sig, payload)` on Arbitrum. The Borsh encoding is identical; `ECDSA.recover(keccak256(borshEncoded), sig)` returns `nearBridgeDerivedAddress`. Signature check passes. [6](#0-5) 

4. Attacker calls `OmniBridge.deploy_token(sig, payload)` on Starknet. `_verify_borsh_signature` passes for the same reason. [7](#0-6) 

5. Attacker calls `deploy_token` on Solana with the same `SignedPayload`. `verify_signature` passes. [8](#0-7) 

6. Token "usdc.near" is now deployed on Arbitrum, Starknet, and Solana without NEAR's per-chain authorization. Legitimate deployment attempts on those chains revert with `ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED`. [9](#0-8) [10](#0-9)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-153)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-298)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
```

**File:** starknet/src/bridge_types.cairo (L36-44)
```text
    fn to_borsh(self: @MetadataPayload) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::Metadata.into());
        borsh_bytes.append(@borsh::encode_byte_array(self.token));
        borsh_bytes.append(@borsh::encode_byte_array(self.name));
        borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
        borsh_bytes.append_byte(*self.decimals);
        borsh_bytes
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L19-26)
```rust
    fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        IncomingMessageType::Metadata.serialize(&mut writer)?;
        self.serialize(&mut writer)?; // borsh encoding
        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
```

**File:** near/omni-types/src/lib.rs (L694-702)
```rust
#[near(serializers = [borsh, json])]
#[derive(Debug, Clone)]
pub struct MetadataPayload {
    pub prefix: PayloadType,
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
```

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
```

**File:** starknet/src/omni_bridge.cairo (L207-209)
```text
            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

**File:** solana/programs/bridge_token_factory/src/state/message/mod.rs (L23-47)
```rust
impl<P: Payload> SignedPayload<P> {
    pub fn verify_signature(
        &self,
        params: P::AdditionalParams,
        derived_near_bridge_address: &[u8; 64],
    ) -> Result<()> {
        let serialized = self.payload.serialize_for_near(params)?;
        let hash = keccak::hash(&serialized);

        let signature_bytes = &self.signature[0..64];

        let signature = libsecp256k1::Signature::parse_standard_slice(signature_bytes)
            .map_err(|_| ProgramError::InvalidArgument)?;
        require!(!signature.s.is_high(), ErrorCode::MalleableSignature);

        let signer = secp256k1_recover(&hash.to_bytes(), self.signature[64], signature_bytes)
            .map_err(|_| error!(ErrorCode::SignatureVerificationFailed))?;

        require!(
            signer.0 == *derived_near_bridge_address,
            ErrorCode::SignatureVerificationFailed
        );

        Ok(())
    }
```
