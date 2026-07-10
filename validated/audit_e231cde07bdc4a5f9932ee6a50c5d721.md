Now I have all the evidence needed. Let me compile the complete finding.

---

### Title
Missing Chain ID in `deployToken` Signed Message Enables Cross-Chain Signature Replay — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`, `starknet/src/bridge_types.cairo`)

---

### Summary

The `deployToken` / `deploy_token` function on EVM, Solana, and StarkNet verifies a NEAR MPC ECDSA signature over a `MetadataPayload` that contains no destination chain ID. In contrast, `finTransfer` / `finalize_transfer` on all three chains correctly binds the signed message to the destination chain by embedding `omniBridgeChainId` / `SOLANA_OMNI_BRIDGE_CHAIN_ID` in the hashed payload. Because the `deployToken` signed message is chain-agnostic, a single valid signature obtained from one chain is cryptographically valid on every other supported chain. Any unprivileged observer can replay it to deploy the same token on chains where NEAR has not authorized deployment, permanently blocking the bridge from ever deploying that token legitimately on those chains.

---

### Finding Description

**Root cause — NEAR signing side**

`log_metadata_callback` in `near/omni-bridge/src/lib.rs` constructs a `MetadataPayload` and submits it to the MPC signer:

```rust
let metadata_payload = MetadataPayload {
    prefix: PayloadType::Metadata,
    token: token_id.to_string(),
    name: metadata.name,
    symbol: metadata.symbol,
    decimals: metadata.decimals,
};
let payload = near_sdk::env::keccak256_array(
    borsh::to_vec(&metadata_payload)...
);
ext_signer::ext(self.mpc_signer.clone())...sign(SignRequest { payload, ... })
``` [1](#0-0) 

The `MetadataPayload` struct contains only `prefix`, `token`, `name`, `symbol`, `decimals` — no chain ID. [2](#0-1) 

**Root cause — EVM verification**

`deployToken` in `OmniBridge.sol` reconstructs the signed message as:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

No `omniBridgeChainId` is included. [3](#0-2) 

Compare with `finTransfer`, which embeds `omniBridgeChainId` twice (for token address and recipient address fields): [4](#0-3) 

**Root cause — Solana verification**

`DeployTokenPayload::serialize_for_near` serializes only `[IncomingMessageType::Metadata, token, name, symbol, decimals]` — no `SOLANA_OMNI_BRIDGE_CHAIN_ID`: [5](#0-4) 

Compare with `FinalizeTransferPayload::serialize_for_near`, which writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` before both the token mint and the recipient pubkey: [6](#0-5) 

**Root cause — StarkNet verification**

`MetadataPayloadTrait::to_borsh()` encodes only `[PayloadType::Metadata, token, name, symbol, decimals]`: [7](#0-6) 

`deploy_token` calls it without any chain ID argument: [8](#0-7) 

Compare with `TransferMessagePayloadTrait::to_borsh(chain_id: u8)`, which embeds `chain_id` twice: [9](#0-8) 

And `fin_transfer` passes `self.omni_bridge_chain_id.read()` to it: [10](#0-9) 

The StarkNet CLAUDE.md explicitly documents chain ID binding as a security property for `fin_transfer` but makes no equivalent claim for `deploy_token`: [11](#0-10) 

---

### Impact Explanation

**Unauthorized token deployment across all chains from a single signature.** The NEAR MPC produces one signature per `log_metadata` call. Because that signature contains no chain binding, it is simultaneously valid on Ethereum, Arbitrum, Base, BNB, Polygon, Solana, StarkNet, and any future chain added to the bridge. An attacker who observes a valid `deployToken` transaction on chain A can immediately replay the same `(signature, MetadataPayload)` on every other chain B, C, D… to deploy the token there without NEAR's per-chain authorization.

**Permanent blocking of legitimate deployment.** Once the attacker deploys the token on chain B, the guard `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` (EVM) / `assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED')` (StarkNet) / the Anchor account-existence check (Solana) will permanently prevent the bridge from ever deploying that token on chain B through the legitimate flow. The bridge loses the ability to control which token contract address is canonical on that chain.

This matches the allowed impact: **High — Proof/signature/MPC verification bypass enabling unauthorized token deployment**.

---

### Likelihood Explanation

The attack requires only:
1. Watching any public chain for a `deployToken` transaction (trivially observable on-chain).
2. Extracting the `(signatureData, MetadataPayload)` arguments.
3. Calling `deployToken` on any other chain with the same arguments.

No special privileges, no leaked keys, no colluding MPC signers. The attacker is a standard unprivileged bridge observer. The bridge supports 7+ chains simultaneously, so every legitimate `deployToken` event on any one chain is an immediate replay opportunity on all others.

---

### Recommendation

Include the destination chain ID in the signed `MetadataPayload`, mirroring the pattern already used by `finTransfer`. On the NEAR signing side, pass the target `ChainKind` into `log_metadata_callback` and add it to `MetadataPayload` before hashing. On each destination chain, prepend the local chain ID byte to the borsh-encoded message before hashing, exactly as done for `finTransfer`:

```solidity
// EVM deployToken — add chain ID to borsh encoding
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // bind to this chain
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent fix in `DeployTokenPayload::serialize_for_near` (Solana) and `MetadataPayloadTrait::to_borsh` (StarkNet).

---

### Proof of Concept

1. NEAR bridge operator calls `log_metadata("usdc.near")` on the NEAR bridge. The MPC signs `keccak256(borsh([Metadata, "usdc.near", "USD Coin", "USDC", 6]))` → `sig_S`.

2. A relayer submits `deployToken(sig_S, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` to the Ethereum bridge. The token is deployed at address `0xAAA...`.

3. An attacker observes this transaction on Ethereum. They extract `sig_S` and the payload.

4. The attacker calls `deployToken(sig_S, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on the Arbitrum bridge. The EVM `deployToken` function reconstructs the identical borsh encoding (no chain ID), hashes it, recovers `nearBridgeDerivedAddress` from `sig_S`, and succeeds. A new token is deployed at `0xBBB...` on Arbitrum — without NEAR's authorization for Arbitrum.

5. The attacker repeats for Base, BNB, Polygon, Solana, and StarkNet using the same `sig_S`.

6. When the NEAR bridge later tries to legitimately deploy USDC on Arbitrum, the call reverts with `ERR_TOKEN_EXIST` because `nearToEthToken["usdc.near"]` is already set to `0xBBB...`. The legitimate deployment is permanently blocked. [12](#0-11) [13](#0-12)

### Citations

**File:** near/omni-bridge/src/lib.rs (L341-360)
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

        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L29-37)
```rust
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
        // 6. fee_recipient
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

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
```

**File:** starknet/CLAUDE.md (L45-46)
```markdown
1. **Chain ID binding**: Destination chain_id encoded in message hash (not in payload) - prevents cross-chain replay
2. **Public `log_metadata`**: Intentionally permissionless for token discovery
```
