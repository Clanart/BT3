### Title
Missing Chain-ID Binding in `deploy_token` Signature Enables Cross-Chain Replay of Token Deployment — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `deployToken` function in `OmniBridge.sol` (and its counterparts in StarkNet and Solana) constructs the MPC-signed payload without including any destination-chain identifier. Because the same NEAR MPC-derived address (`nearBridgeDerivedAddress`) is shared across all EVM deployments, a valid `deploy_token` signature observed on one chain (e.g., Ethereum) can be replayed verbatim on every other EVM chain (Arbitrum, Base, BNB, Polygon, HyperEvm, Abs) and cross-chain to StarkNet and Solana, deploying the token on those chains without per-chain authorization.

---

### Finding Description

**EVM — `OmniBridge.sol::deployToken`**

The borsh payload that is hashed and verified against `nearBridgeDerivedAddress` is:

```solidity
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

No `omniBridgeChainId` is present. [1](#0-0) 

Contrast this with `finTransfer`, which explicitly embeds `bytes1(omniBridgeChainId)` twice in the signed payload (once for the token address and once for the recipient address), binding the signature to a specific chain:

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
...
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

**StarkNet — `bridge_types.cairo::MetadataPayload::to_borsh`**

`deploy_token` calls `_verify_borsh_signature(ref self, @payload.to_borsh(), signature)` — no chain-ID argument. [3](#0-2) 

`MetadataPayload::to_borsh()` encodes only `PayloadType::Metadata || token || name || symbol || decimals` — no `chain_id` field: [4](#0-3) 

Compare with `TransferMessagePayload::to_borsh(chain_id)`, which takes and embeds `chain_id` twice: [5](#0-4) 

And `fin_transfer` passes `self.omni_bridge_chain_id.read()` explicitly: [6](#0-5) 

**Solana — `deploy_token.rs::DeployTokenPayload::serialize_for_near`**

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    let mut writer = BufWriter::new(Vec::with_capacity(DEFAULT_SERIALIZER_CAPACITY));
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?; // borsh encoding — no SOLANA_OMNI_BRIDGE_CHAIN_ID
    ...
}
``` [7](#0-6) 

Compare with `FinalizeTransferPayload::serialize_for_near`, which writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` before both the token and recipient fields: [8](#0-7) 

---

### Impact Explanation

The NEAR MPC signer produces a single `deploy_token` signature for a given `(token, name, symbol, decimals)` tuple. Because the signed bytes are identical across all chains, any party who observes the signature in a transaction on chain A (e.g., Ethereum) can immediately submit it to chain B (e.g., Arbitrum, Base, BNB, Polygon, HyperEvm, Abs, StarkNet, Solana) and deploy the same bridge token there without the NEAR MPC ever authorizing a deployment on chain B.

Consequences:
1. **Unauthorized token deployment**: Bridge tokens are deployed on chains the protocol has not explicitly authorized, bypassing the intended per-chain governance.
2. **Blocking legitimate deployment**: Once deployed, the `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` guard prevents any future legitimate deployment of the same token on that chain. [9](#0-8) 
3. **Unauthorized bridge path creation**: If the attacker then submits proof of the unauthorized deployment to the NEAR bridge, NEAR registers the token address for that chain and begins signing `finTransfer` messages for it, creating an unauthorized cross-chain bridge path.

This matches the allowed impact: **High — Proof, signature, MPC verification bypass enabling unauthorized token deployment.**

---

### Likelihood Explanation

- The `deploy_token` transaction and its signature are publicly visible on-chain.
- Any observer can extract the signature bytes and replay them on any other supported chain.
- No privileged access, leaked keys, or colluding parties are required.
- The only precondition is that the target chain has not yet deployed the token — a common state for newly listed tokens.

Likelihood: **High**.

---

### Recommendation

Include the destination chain identifier in the borsh-encoded payload for `deploy_token`, exactly as `finTransfer` does. For EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // <-- add this
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent fix in `MetadataPayload::to_borsh()` in StarkNet (pass and embed `chain_id`) and in `DeployTokenPayload::serialize_for_near` in Solana (write `SOLANA_OMNI_BRIDGE_CHAIN_ID` before the token fields). The NEAR MPC signer must also be updated to include the target chain ID when generating `deploy_token` signatures.

---

### Proof of Concept

1. The NEAR backend signs a `deploy_token` payload for `"usdc.near"` on Ethereum (chain ID `0`). The signed bytes are:
   ```
   \x01 || borsh("usdc.near") || borsh("USD Coin") || borsh("USDC") || \x06
   ```
2. This transaction is submitted to the Ethereum `OmniBridge`, deploying the USDC bridge token. The signature `sig` is now public.
3. An attacker calls `deployToken(sig, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on the Arbitrum `OmniBridge` (a separate contract with `omniBridgeChainId = 3`).
4. The Arbitrum contract constructs the identical borsh bytes (no chain ID in the payload), recovers the same `nearBridgeDerivedAddress`, and accepts the signature — deploying an unauthorized USDC bridge token on Arbitrum.
5. The attacker repeats for Base, BNB, Polygon, StarkNet, and Solana using the same `sig`.
6. All six chains now have a registered bridge token for `"usdc.near"` deployed without per-chain MPC authorization, and the `ERR_TOKEN_EXIST` guard permanently blocks any future legitimate deployment on those chains.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-313)
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

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
```

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
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
