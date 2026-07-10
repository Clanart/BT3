### Title
`deployToken` Signed Message Omits Chain ID, Enabling Cross-Chain Signature Replay for Unauthorized Token Deployment — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The NEAR MPC signature verified in `deployToken`/`deploy_token` does not include any chain-specific identifier (chain ID or contract address) in the signed message. Because the borsh-encoded payload for token deployment is structurally identical across EVM, Starknet, and Solana, a valid signature obtained from one chain can be replayed verbatim on any other chain that shares the same `nearBridgeDerivedAddress`. This enables unauthorized token deployment on chains where the NEAR MPC never authorized it.

---

### Finding Description

Every chain's `finTransfer`/`finalize_transfer` correctly binds the signed message to the destination chain by embedding the chain ID in the borsh payload. For example, in EVM `finTransfer`:

```solidity
bytes1(omniBridgeChainId),   // line 294 — token field prefix
...
bytes1(omniBridgeChainId),   // line 297 — recipient field prefix
``` [1](#0-0) 

However, `deployToken` on EVM builds its signed payload as:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),  // = 0x01
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
``` [2](#0-1) 

No `omniBridgeChainId` is present. The Starknet `MetadataPayloadTrait::to_borsh` produces the identical structure:

```cairo
borsh_bytes.append_byte(PayloadType::Metadata.into()); // = 1
borsh_bytes.append(@borsh::encode_byte_array(self.token));
borsh_bytes.append(@borsh::encode_byte_array(self.name));
borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
borsh_bytes.append_byte(*self.decimals);
``` [3](#0-2) 

And Solana's `DeployTokenPayload::serialize_for_near` does the same:

```rust
IncomingMessageType::Metadata.serialize(&mut writer)?;  // variant index 1
self.serialize(&mut writer)?;  // token, name, symbol, decimals — no chain ID
``` [4](#0-3) 

All three chains use the same NEAR MPC key (`nearBridgeDerivedAddress` / `omni_bridge_derived_address` / `derived_near_bridge_address`). The signed byte sequence `[0x01 || borsh(token) || borsh(name) || borsh(symbol) || decimals]` is byte-for-byte identical across EVM, Starknet, and Solana for the same token metadata. Therefore, a single NEAR MPC signature is valid on all three chains simultaneously.

The Starknet `fin_transfer` correctly passes `chain_id` to `to_borsh`: [5](#0-4) 

And `TransferMessagePayloadTrait::to_borsh` embeds it twice: [6](#0-5) 

The asymmetry is clear: `finTransfer` is chain-bound; `deployToken` is not.

---

### Impact Explanation

An attacker who observes a legitimate `deployToken` call on chain A can extract the raw signature from the calldata and submit it to `deployToken`/`deploy_token` on chain B with the same `MetadataPayload`. The signature check passes because the hash is identical. The token is then registered as a bridge token on chain B without NEAR MPC authorization for that chain.

Consequences:
- A token is deployed on a chain where the protocol never intended it, registering it in `nearToEthToken`/`near_to_starknet_token`/wrapped mint mappings.
- Once registered as a bridge token, any subsequent legitimate `finTransfer` for that token on chain B will mint tokens to recipients — the NEAR side cannot distinguish between an authorized and a replayed deployment.
- If the token was supposed to be mapped to an existing native token via `addCustomToken` on chain B, the replay pre-empts that mapping, permanently locking the bridge into using a worthless new token contract instead of the real one, breaking collateralization.

This matches the allowed High impact: **"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution."**

---

### Likelihood Explanation

- The attack requires no privileges: any public observer of on-chain transactions can extract a `deployToken` signature.
- The same NEAR MPC key is used across all chains by design.
- The attacker only needs to front-run or replay the transaction on a different chain before the token is legitimately deployed there.
- Likelihood is **Medium-High**: the window exists whenever a new token is being deployed on one chain but not yet on others.

---

### Recommendation

Include the destination chain ID in the `deployToken` borsh payload, mirroring the pattern already used in `finTransfer`. For EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent fix in `MetadataPayloadTrait::to_borsh` (Starknet) and `DeployTokenPayload::serialize_for_near` (Solana), and update the NEAR MPC signing logic to include the destination chain ID when producing `deployToken` signatures.

---

### Proof of Concept

1. NEAR MPC signs a `deployToken` message for token `"usdc.near"` targeting EVM Ethereum (`omniBridgeChainId = 0x02`). The signed bytes are `keccak256([0x01, borsh("usdc.near"), borsh("USD Coin"), borsh("USDC"), 6])`.
2. A relayer submits this to `OmniBridge.deployToken` on Ethereum — succeeds legitimately.
3. Attacker extracts the 65-byte signature from the Ethereum transaction calldata.
4. Attacker calls `OmniBridge.deployToken` on BSC (a separate EVM deployment, `omniBridgeChainId = 0x04`) with the same `MetadataPayload` and the extracted signature.
5. EVM BSC computes `keccak256([0x01, borsh("usdc.near"), borsh("USD Coin"), borsh("USDC"), 6])` — identical hash, identical signature — `ECDSA.recover` returns `nearBridgeDerivedAddress`. Signature check passes.
6. `"usdc.near"` is now registered as a bridge token on BSC without NEAR MPC authorization for BSC. Any future `finTransfer` for `"usdc.near"` on BSC will mint from this unauthorized contract.
7. The same signature can additionally be replayed on Starknet `deploy_token` and Solana `deploy_token` for the same effect.

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

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
```
