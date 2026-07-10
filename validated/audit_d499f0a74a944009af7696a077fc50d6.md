### Title
Cross-Chain Replay of `deploy_token` Signature Enables Unauthorized Token Deployment on Any Chain — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The Borsh-encoded message signed by the NEAR MPC for `deploy_token` / `deployToken` is **byte-for-byte identical** across EVM, Starknet, and Solana. It contains no chain identifier. A valid signature observed on one chain can be extracted from public calldata and replayed on every other chain to deploy bridge tokens without per-chain NEAR MPC authorization.

---

### Finding Description

Every chain's `deploy_token` path constructs the same signed payload:

**EVM** (`OmniBridge.sol` lines 142–148):
```
[PayloadType.Metadata (0x01)] [token] [name] [symbol] [decimals]
```

**Starknet** (`bridge_types.cairo` `MetadataPayloadImpl::to_borsh`, lines 36–44):
```
[PayloadType::Metadata (0x01)] [token] [name] [symbol] [decimals]
```

**Solana** (`deploy_token.rs` `DeployTokenPayload::serialize_for_near`, lines 19–26):
```
[IncomingMessageType::Metadata (0x01)] [token] [name] [symbol] [decimals]
```

All three produce the same byte sequence for the same token metadata. The `keccak256` hash is therefore identical, and a single ECDSA signature over it satisfies `ECDSA.recover(...) == nearBridgeDerivedAddress` on EVM, `verify_eth_signature(...)` on Starknet, and `secp256k1_recover(...)` on Solana.

This is in direct contrast to `fin_transfer`, where every chain explicitly binds the chain ID into the signed message:

- EVM `finTransfer` (lines 294, 297): `bytes1(omniBridgeChainId)` appears twice in the Borsh encoding.
- Starknet `TransferMessagePayloadImpl::to_borsh(chain_id)` (lines 67, 70): `chain_id` appears twice.
- Solana `FinalizeTransferPayload::serialize_for_near` (lines 30, 35): `SOLANA_OMNI_BRIDGE_CHAIN_ID` appears twice.

The asymmetry is the root cause: `fin_transfer` is chain-bound; `deploy_token` is not.

---

### Impact Explanation

An attacker who observes a valid `deployToken` transaction on EVM (the signature is in public calldata) can:

1. Extract the 65-byte ECDSA signature.
2. Call `deploy_token` on Starknet with the same `MetadataPayload` and signature — `_verify_borsh_signature` passes because the hash is identical.
3. Call `deploy_token` on Solana with the same payload and signature — `verify_signature` passes for the same reason.

Each successful call deploys a new bridge token on the target chain and writes the NEAR token ID → chain token address mapping. The NEAR bridge reads the emitted `DeployToken` event and registers the token for that chain, after which the NEAR MPC will sign `fin_transfer` messages for it.

This is a **signature bypass enabling unauthorized token deployment**: the NEAR MPC signed once for one chain; the attacker uses that signature to authorize deployment on all other chains without any additional MPC involvement. This matches the High impact category: *"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution."*

---

### Likelihood Explanation

- The signature is in public calldata of any `deployToken` EVM transaction; no privileged access is required.
- The attacker only needs to submit a standard transaction on the target chain.
- The `ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED` guard only prevents double-deployment on the **same** chain; it does not block cross-chain replay.
- Any unprivileged user who monitors the bridge can execute this immediately after the first `deployToken` transaction appears on any chain.

Likelihood: **High** — zero-knowledge, zero-cost, fully permissionless.

---

### Recommendation

Bind the destination chain identifier into the `deploy_token` signed message, mirroring the pattern already used in `fin_transfer`. Concretely:

**EVM** — add `bytes1(omniBridgeChainId)` to the Borsh encoding before or after the token string:
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),
    Borsh.encodeString(metadata.token),
    ...
);
```

**Starknet** — pass `chain_id` into `to_borsh` and prepend it:
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
+   borsh_bytes.append_byte(chain_id);
    ...
}
```

**Solana** — include `SOLANA_OMNI_BRIDGE_CHAIN_ID` in `serialize_for_near`:
```rust
IncomingMessageType::Metadata.serialize(&mut writer)?;
+ writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
self.serialize(&mut writer)?;
```

The NEAR MPC signing logic must be updated in parallel to include the target chain ID when producing the `MetadataPayload` signature, so that each chain's deployment requires a distinct, chain-bound signature.

---

### Proof of Concept

1. Submit a `deployToken` call on EVM for NEAR token `"usdc.near"` with a valid NEAR MPC signature `sig`.
2. Observe the transaction on-chain; extract `sig` from calldata.
3. Construct the identical `MetadataPayload{token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6}`.
4. Call Starknet `deploy_token(sig, payload)` — `_verify_borsh_signature` computes the same keccak hash and recovers the same `nearBridgeDerivedAddress`; the call succeeds and deploys a Starknet bridge token.
5. Call Solana `deploy_token` with the same payload and `sig` — `verify_signature` recovers the same `derived_near_bridge_address`; the call succeeds and creates a Solana SPL mint.

Both steps 4 and 5 succeed with zero additional NEAR MPC involvement, using only the single signature produced for step 1.

---

**Affected files and lines:** [1](#0-0) [2](#0-1) [3](#0-2) 

**Contrast — chain ID correctly bound in `fin_transfer`:** [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-149)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-309)
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
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);
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

**File:** starknet/src/bridge_types.cairo (L61-84)
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
        match self.fee_recipient {
            Option::None => { borsh_bytes.append_byte(0); },
            Option::Some(fee_recipient) => {
                borsh_bytes.append_byte(1);
                borsh_bytes.append(@borsh::encode_byte_array(fee_recipient));
            },
        }
        match self.message {
            Option::None => {},
            Option::Some(message) => { borsh_bytes.append(@borsh::encode_byte_array(message)); },
        }
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L28-36)
```rust
        self.transfer_id.origin_nonce.serialize(&mut writer)?;
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
```
