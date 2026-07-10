### Title
`deployToken` Metadata Signature Lacks Chain ID Binding — Cross-Chain Replay Deploys Unauthorized Bridge Tokens - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

The `deployToken` function in `OmniBridge.sol`, `deploy_token` in `starknet/src/omni_bridge.cairo`, and `DeployTokenPayload::serialize_for_near` in Solana's `deploy_token.rs` all hash the `MetadataPayload` without including the destination chain ID. Because the Omni Bridge is deployed on multiple EVM chains (Ethereum, Arbitrum, Base, BSC) sharing the same `nearBridgeDerivedAddress`, a `deployToken` signature observed on one chain can be replayed verbatim on every other chain. This permanently blocks legitimate token deployment on the replayed chains and corrupts the bridge's token registry.

---

### Finding Description

**EVM — `OmniBridge.sol` `deployToken`:**

The Borsh-encoded message hashed for signature verification is:

```
PayloadType.Metadata | token | name | symbol | decimals
```

`omniBridgeChainId` is **absent**. [1](#0-0) 

Compare this to `finTransfer`, which correctly embeds `omniBridgeChainId` twice (for token address and recipient): [2](#0-1) 

The contract stores `omniBridgeChainId` and uses it in `finTransfer`, but the `deployToken` path never reads it for signature binding. [3](#0-2) 

**Starknet — `bridge_types.cairo` `MetadataPayload::to_borsh`:**

`to_borsh()` for `MetadataPayload` encodes only `PayloadType::Metadata | token | name | symbol | decimals` — no `chain_id`. [4](#0-3) 

The Starknet CLAUDE.md explicitly documents chain ID binding as a security property for `fin_transfer`, but `deploy_token` is not protected: [5](#0-4) 

`deploy_token` calls `_verify_borsh_signature` with the chain-ID-free `to_borsh()` output: [6](#0-5) 

**Solana — `deploy_token.rs` `DeployTokenPayload::serialize_for_near`:**

The Solana serialization also omits `SOLANA_OMNI_BRIDGE_CHAIN_ID` from the metadata payload, unlike `FinalizeTransferPayload::serialize_for_near` which includes it: [7](#0-6) [8](#0-7) 

---

### Impact Explanation

1. **Unauthorized token deployment**: An attacker observes a legitimate `deployToken` transaction on chain A (e.g., Ethereum), extracts `signatureData` and `metadata` from calldata, and replays the call on chain B (e.g., Arbitrum). The signature passes because the hash is identical across chains.

2. **Permanent blocking of legitimate deployment**: After the replay, `nearToEthToken[metadata.token]` is set on chain B. Any future legitimate `deployToken` call for that NEAR token ID on chain B reverts with `ERR_TOKEN_EXIST`. [9](#0-8) 

3. **Registry corruption**: The replayed token contract on chain B is unknown to NEAR's bridge state. NEAR cannot issue valid `finTransfer` signatures for it (since NEAR never registered the chain B token address). The token is permanently orphaned, and the NEAR token ID is permanently locked out of chain B.

This satisfies: **High — Cross-chain replay enabling unauthorized token deployment and permanent irrecoverable lock of the token deployment flow on the target chain.**

---

### Likelihood Explanation

- The Omni Bridge is confirmed deployed on Ethereum, Arbitrum, Base, and BSC (evidenced by `.openzeppelin` deployment files).
- All deployments share the same `nearBridgeDerivedAddress` (NEAR MPC derived key).
- `deployToken` calldata is publicly visible on-chain.
- No special knowledge or privilege is required — any observer of a `deployToken` transaction can replay it.
- The attack is a single transaction requiring only gas on the target chain.

---

### Recommendation

Include `omniBridgeChainId` in the Borsh-encoded message hash for `deployToken`, mirroring the pattern already used in `finTransfer`:

**EVM (`OmniBridge.sol`):**
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals),
    bytes1(omniBridgeChainId)   // ADD THIS
);
```

**Starknet (`bridge_types.cairo`):**
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    // ... existing fields ...
    borsh_bytes.append_byte(chain_id);  // ADD THIS
    borsh_bytes
}
```

**Solana (`deploy_token.rs`):**
```rust
writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;  // ADD THIS
IncomingMessageType::Metadata.serialize(&mut writer)?;
self.serialize(&mut writer)?;
```

The NEAR MPC signer must also include the destination chain ID when signing `MetadataPayload` messages, consistent with how it already does for `TransferMessagePayload`.

---

### Proof of Concept

1. NEAR MPC signs a `MetadataPayload` for token `wrap.near` (name=`wNEAR`, symbol=`wNEAR`, decimals=24) destined for Ethereum (`omniBridgeChainId=0`). The signature `S` is submitted in a `deployToken` call on Ethereum — transaction is public.

2. Attacker extracts `S` and `metadata` from Ethereum calldata.

3. Attacker calls `deployToken(S, metadata)` on Arbitrum (`omniBridgeChainId=2`). The hash computed is identical:
   ```
   keccak256(0x01 | borsh("wrap.near") | borsh("wNEAR") | borsh("wNEAR") | 0x18)
   ```
   No chain ID is in the hash, so `ECDSA.recover(hashed, S) == nearBridgeDerivedAddress` passes.

4. A `BridgeToken` proxy for `wrap.near` is deployed on Arbitrum. `nearToEthToken["wrap.near"]` is set to the new proxy address.

5. NEAR's bridge state has no record of this Arbitrum token address. NEAR cannot issue `finTransfer` signatures for `wrap.near` on Arbitrum. Any future legitimate `deployToken` for `wrap.near` on Arbitrum reverts with `ERR_TOKEN_EXIST`. The token is permanently orphaned.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L42-42)
```text
    uint8 public omniBridgeChainId;
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
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

**File:** starknet/CLAUDE.md (L45-45)
```markdown
1. **Chain ID binding**: Destination chain_id encoded in message hash (not in payload) - prevents cross-chain replay
```

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L30-36)
```rust
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
```
