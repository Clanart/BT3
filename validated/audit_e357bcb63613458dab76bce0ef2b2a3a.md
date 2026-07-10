### Title
Cross-Chain Replay of MPC-Signed `MetadataPayload` Enables Unauthorized Token Deployment on Unintended Chains - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `MetadataPayload` signed by the NEAR MPC signer for `deployToken` / `deploy_token` does not include a destination chain identifier. The borsh encoding is byte-for-byte identical across EVM, Starknet, and Solana, and the same `nearBridgeDerivedAddress` (derived from the NEAR MPC key) is used on all chains. A valid signature observed on one chain can be replayed verbatim on any other chain to deploy the same token without authorization.

---

### Finding Description

The NEAR bridge signs a `MetadataPayload` when `log_metadata` is called. The signed payload contains only:

```
prefix (1 byte) | token (borsh string) | name (borsh string) | symbol (borsh string) | decimals (1 byte)
```

**EVM** (`OmniBridge.sol`, `deployToken`): [1](#0-0) 

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

No `omniBridgeChainId` is included.

**Starknet** (`bridge_types.cairo`, `MetadataPayloadTrait::to_borsh`): [2](#0-1) 

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
```

No `omni_bridge_chain_id` is included. Compare with `TransferMessagePayloadTrait::to_borsh` which explicitly takes and embeds `chain_id`: [3](#0-2) 

**Solana** (`deploy_token.rs`, `DeployTokenPayload::serialize_for_near`): [4](#0-3) 

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?; // token + name + symbol + decimals
```

No `SOLANA_OMNI_BRIDGE_CHAIN_ID` is included. Compare with `FinalizeTransferPayload::serialize_for_near` which writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` for both the token and recipient fields: [5](#0-4) 

The three encodings produce **identical byte sequences** for the same token metadata. Since the same NEAR MPC key (`nearBridgeDerivedAddress`) is configured on all chains, the ECDSA/secp256k1 signature is valid on every chain simultaneously.

The only per-chain guard is an idempotency check (token already deployed), not a domain-separation check:

- EVM: `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")` [6](#0-5) 
- Starknet: `assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED')` [7](#0-6) 
- Solana: PDA `init` constraint on `[WRAPPED_MINT_SEED, token_hash]` [8](#0-7) 

These checks prevent re-deployment on the **same** chain but do nothing to prevent replay on a **different** chain.

---

### Impact Explanation

An attacker who observes a valid `deployToken` signature (emitted as a NEAR event via `sign_log_metadata_callback`): [9](#0-8) 

can replay it on any other chain where that token has not yet been deployed. This:

1. **Deploys the token on an unintended chain** without authorization from the bridge operator.
2. **Permanently occupies the token slot** on that chain — the idempotency check (`ERR_TOKEN_EXIST` / PDA already initialized) will block all future legitimate deployment attempts for that token on that chain.
3. **Causes state desync** between NEAR and the destination chain if the deployment occurs before NEAR has registered the chain as a valid destination, potentially making the token unclaimable on that chain.

This matches the allowed High impact: *"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, **token deployment**, or message execution."*

---

### Likelihood Explanation

- The MPC signature is published as a public NEAR event (`LogMetadataEvent`) and is observable by any network participant.
- No privileged access is required — any unprivileged user can call `deployToken` on any chain with the replayed signature.
- The attack requires only reading a NEAR event and submitting a transaction on the target chain.
- The bridge is live across multiple EVM chains, Starknet, and Solana simultaneously, making cross-chain replay immediately actionable.

---

### Recommendation

Include the destination chain identifier in the signed `MetadataPayload`. Mirror the pattern already used in `TransferMessagePayload`:

- **EVM**: Add `bytes1(omniBridgeChainId)` to the borsh-encoded metadata hash in `deployToken`.
- **Starknet**: Pass `chain_id` into `MetadataPayloadTrait::to_borsh` and append it, as is already done in `TransferMessagePayloadTrait::to_borsh`.
- **Solana**: Write `SOLANA_OMNI_BRIDGE_CHAIN_ID` into `DeployTokenPayload::serialize_for_near`, as is already done in `FinalizeTransferPayload::serialize_for_near`.
- **NEAR MPC signer**: Include the destination `ChainKind` in the `MetadataPayload` before hashing and signing.

---

### Proof of Concept

1. Call `log_metadata("token-x.near")` on the NEAR bridge. The MPC signer produces a signature `S` over `borsh(Metadata | "token-x.near" | "Token X" | "TX" | 18)`. This signature is emitted as a public NEAR event.

2. A relayer submits `(S, payload)` to `deployToken` on Ethereum. The token is deployed at address `0xAAA...` on Ethereum. The NEAR bridge registers `(Eth, "token-x.near") → 0xAAA...`.

3. An attacker observes `S` from the NEAR event. The attacker submits the **identical** `(S, payload)` to `deploy_token` on Starknet. The signature verification passes because:
   - The borsh encoding is identical (no chain ID in either encoding).
   - `omni_bridge_derived_address` on Starknet equals `nearBridgeDerivedAddress` on Ethereum (same NEAR MPC key). [10](#0-9) 

4. The token is deployed on Starknet at a deterministic address. `near_to_starknet_token[hash("token-x.near")]` is now set.

5. When the legitimate bridge operator later tries to deploy `token-x.near` on Starknet, the call reverts with `ERR_TOKEN_ALREADY_DEPLOYED`. The token slot is permanently occupied by the attacker's deployment, and the bridge operator cannot re-deploy it without an admin intervention that the contract does not provide.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L155-158)
```text
        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
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

**File:** starknet/src/bridge_types.cairo (L61-68)
```text
    fn to_borsh(self: @TransferMessagePayload, chain_id: u8) -> ByteArray {
        let mut borsh_bytes: ByteArray = Default::default();
        borsh_bytes.append_byte(PayloadType::TransferMessage.into());
        borsh_bytes.append(@borsh::encode_u64(*self.destination_nonce));
        borsh_bytes.append_byte(*self.origin_chain);
        borsh_bytes.append(@borsh::encode_u64(*self.origin_nonce));
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.token_address));
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

**File:** starknet/src/omni_bridge.cairo (L202-209)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs (L45-53)
```rust
    #[account(
        init,
        payer = common.payer,
        seeds = [WRAPPED_MINT_SEED, data.payload.token.to_hashed_bytes().as_ref()],
        bump,
        mint::decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, data.payload.decimals),
        mint::authority = authority,
    )]
    pub mint: Box<Account<'info, Mint>>,
```

**File:** near/omni-bridge/src/lib.rs (L368-384)
```rust
    #[private]
    #[result_serializer(borsh)]
    pub fn sign_log_metadata_callback(
        &self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] metadata_payload: MetadataPayload,
    ) {
        if let Ok(signature) = call_result {
            env::log_str(
                &OmniBridgeEvent::LogMetadataEvent {
                    signature,
                    metadata_payload,
                }
                .to_log_string(),
            );
        }
    }
```
