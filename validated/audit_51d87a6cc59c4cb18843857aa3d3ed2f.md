### Title
Cross-Chain Replay of `deploy_token` MPC Signature Due to Missing Destination Chain ID in Signed Payload - (Files: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `MetadataPayload` signed by the NEAR MPC for `deploy_token` does not include a destination chain identifier. The identical Borsh-encoded payload and signature are accepted verbatim by EVM, StarkNet, and Solana bridge contracts. An unprivileged attacker who observes a valid `deploy_token` signature on one chain can replay it on every other chain, deploying the token without NEAR's authorization for those chains and permanently blocking legitimate deployment there.

---

### Finding Description

When NEAR authorizes a token deployment, `log_metadata_callback` constructs a `MetadataPayload` and requests an MPC signature over its Borsh encoding:

```
[PayloadType::Metadata (1 byte)] | [token string] | [name string] | [symbol string] | [decimals (1 byte)]
``` [1](#0-0) 

This is the exact same encoding used by all three destination chains when verifying the signature.

**EVM** (`OmniBridge.sol`):
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) { revert InvalidSignature(); }
``` [2](#0-1) 

**StarkNet** (`bridge_types.cairo`):
```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
}
``` [3](#0-2) 

**Solana** (`deploy_token.rs`):
```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?; // token, name, symbol, decimals — no chain ID
}
``` [4](#0-3) 

**No chain ID appears in any of these encodings.** Compare this with `TransferMessagePayload`, which explicitly binds to the destination chain by including `omniBridgeChainId` twice in the signed bytes: [5](#0-4) [6](#0-5) 

Because the `MetadataPayload` encoding is chain-agnostic, a single MPC signature is cryptographically valid on every chain simultaneously.

Each chain's `deploy_token` uses the token-ID existence check as its only replay guard:

- EVM: `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` [7](#0-6) 
- StarkNet: `assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED')` [8](#0-7) 

This guard only prevents re-deployment on the *same* chain; it does nothing to prevent the same signature from being used on a *different* chain.

---

### Impact Explanation

An attacker who replays a `deploy_token` signature on an unintended chain causes:

1. **Unauthorized token deployment**: The token is deployed on a chain NEAR never authorized, without NEAR's `token_id_to_address` mapping being updated for that chain.
2. **Permanent blocking of legitimate deployment**: Because `ERR_TOKEN_ALREADY_DEPLOYED` / `ERR_TOKEN_EXIST` is a hard revert with no admin override for bridge tokens (there is no `removeToken` for non-custom tokens on EVM or StarkNet), the legitimate relayer can never deploy the token on that chain again.
3. **Permanent loss of bridging capability**: With the token deployed but unrecognized by NEAR, NEAR will never sign `fin_transfer` messages for it on that chain. The token is permanently orphaned and the chain is permanently blocked for that token.

This matches the allowed impact: **High — MPC signature verification bypass enabling unauthorized token deployment**, and **Critical — permanent freezing of bridge flows** for the affected token/chain pair.

---

### Likelihood Explanation

- The NEAR `LogMetadataEvent` (emitted by `sign_log_metadata_callback`) is a public on-chain event containing the full signature and payload. [9](#0-8) 
- Any observer can extract the signature and call `deploy_token` on any other chain before the legitimate relayer does.
- No privileged access is required. The attacker only needs to monitor NEAR events and submit a transaction on the target chain.
- The attack is a simple one-transaction replay; it requires no special tooling beyond a standard RPC client.

---

### Recommendation

Include the destination chain ID in the `MetadataPayload` Borsh encoding, mirroring the pattern already used in `TransferMessagePayload`. On NEAR, pass the target `ChainKind` into `log_metadata_callback` and encode it into the signed payload. Each destination chain contract must then verify that the chain ID in the payload matches its own `omniBridgeChainId`.

```solidity
// EVM fix — add chain ID to the signed encoding
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // <-- add this
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent change to StarkNet's `MetadataPayloadTrait::to_borsh`, Solana's `DeployTokenPayload::serialize_for_near`, and the NEAR signing path in `log_metadata_callback`.

---

### Proof of Concept

1. NEAR admin calls `log_metadata("usdc.near")` on the NEAR bridge.
2. NEAR MPC signs `keccak256(borsh([0x01, "usdc.near", "USD Coin", "USDC", 6]))` and emits `LogMetadataEvent { signature, metadata_payload }`.
3. Legitimate relayer calls `deployToken(sig, payload)` on Ethereum — token deployed at address `0xAAA`.
4. **Attacker** observes the same `LogMetadataEvent`, extracts `sig` and `payload`, and calls `deploy_token(sig, payload)` on StarkNet before the legitimate relayer does.
5. StarkNet verifies `_verify_borsh_signature` — passes, because the encoding is identical. [10](#0-9) 
6. Token is deployed on StarkNet at some address `0xBBB`; `near_to_starknet_token["usdc.near"] = 0xBBB`.
7. NEAR's `token_id_to_address[(Strk, "usdc.near")]` is never set — NEAR will not sign `fin_transfer` for this token on StarkNet.
8. When the legitimate relayer later tries to deploy USDC on StarkNet, it reverts with `ERR_TOKEN_ALREADY_DEPLOYED`.
9. USDC can never be bridged to StarkNet. The state is permanent with no admin recovery path.

### Citations

**File:** near/omni-bridge/src/lib.rs (L341-351)
```rust
        let metadata_payload = MetadataPayload {
            prefix: PayloadType::Metadata,
            token: token_id.to_string(),
            name: metadata.name,
            symbol: metadata.symbol,
            decimals: metadata.decimals,
        };

        let payload = near_sdk::env::keccak256_array(
            borsh::to_vec(&metadata_payload).near_expect(BridgeError::Borsh),
        );
```

**File:** near/omni-bridge/src/lib.rs (L375-383)
```rust
        if let Ok(signature) = call_result {
            env::log_str(
                &OmniBridgeEvent::LogMetadataEvent {
                    signature,
                    metadata_payload,
                }
                .to_log_string(),
            );
        }
```

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

**File:** starknet/src/bridge_types.cairo (L61-71)
```text
    fn to_borsh(self: @TransferMessagePayload, chain_id: u8) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::TransferMessage.into());
        borsh_bytes.append(@borsh::encode_u64(*self.destination_nonce));
        borsh_bytes.append_byte(*self.origin_chain);
        borsh_bytes.append(@borsh::encode_u64(*self.origin_nonce));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.token_address));
        borsh_bytes.append(@borsh::encode_u128(*self.amount));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.recipient));
```

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L19-27)
```rust
    fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
        let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
        IncomingMessageType::Metadata.serialize(&mut writer)?;
        self.serialize(&mut writer)?; // borsh encoding
        writer
            .into_inner()
            .map_err(|_| error!(ErrorCode::InvalidArgs))
    }
}
```

**File:** starknet/src/omni_bridge.cairo (L202-209)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```
