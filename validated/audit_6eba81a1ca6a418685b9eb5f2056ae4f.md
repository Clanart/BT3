### Title
MetadataPayload Signature Missing Destination Chain ID Enables Cross-Chain Replay of `deployToken` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The NEAR bridge signs a `MetadataPayload` (token, name, symbol, decimals) with no destination chain identifier. The identical signature is accepted by every destination chain's `deployToken` / `deploy_token` entry point. An unprivileged observer can replay a signature produced for one chain on any other chain, permanently occupying the token slot and blocking the legitimate deployment of that NEAR token on the target chain.

---

### Finding Description

**How the signature is produced — NEAR side**

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
    borsh::to_vec(&metadata_payload).near_expect(BridgeError::Borsh),
);
``` [1](#0-0) 

The `MetadataPayload` struct carries no destination chain field: [2](#0-1) 

The resulting signature and payload are emitted publicly in a `LogMetadataEvent` on NEAR: [3](#0-2) 

**How the signature is verified — destination chains (all omit chain ID)**

*EVM* — `deployToken` hashes only `PayloadType.Metadata | token | name | symbol | decimals`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
``` [4](#0-3) 

*StarkNet* — `MetadataPayload.to_borsh()` likewise omits chain ID:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
``` [5](#0-4) 

*Solana* — `DeployTokenPayload::serialize_for_near` omits chain ID:

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?; // borsh encoding
``` [6](#0-5) 

**Contrast with `finTransfer` — chain ID IS included**

`finTransfer` on EVM encodes `omniBridgeChainId` twice (for token chain and recipient chain): [7](#0-6) 

`TransferMessagePayload.to_borsh(chain_id)` on StarkNet also includes `chain_id` twice: [8](#0-7) 

`FinalizeTransferPayload::serialize_for_near` on Solana includes `SOLANA_OMNI_BRIDGE_CHAIN_ID` twice: [9](#0-8) 

The `MetadataPayload` path is the only signing path that lacks this domain binding.

---

### Impact Explanation

When NEAR signs a `MetadataPayload` for token `foo.near` (e.g., to deploy it on EVM chain A), the resulting signature is equally valid on EVM chain B, StarkNet, and Solana, because the hash is identical across all chains.

An attacker who replays the signature on chain B calls `deployToken` / `deploy_token` successfully:

- **EVM**: `nearToEthToken[metadata.token]` is set to the replayed proxy address; subsequent legitimate calls revert with `ERR_TOKEN_EXIST`.
- **StarkNet**: `near_to_starknet_token[token_id_hash]` is set; subsequent calls revert with `ERR_TOKEN_ALREADY_DEPLOYED`. There is no admin function to clear this mapping. [10](#0-9) 

- **Solana**: the mint account is created; re-initialization is blocked by the program.

The replayed token has no backing on NEAR (NEAR never registers the replayed chain B address), so `finTransfer` to chain B for that token can never succeed. The token is permanently undeployable on the target chain through the normal bridge flow, permanently freezing the ability of users to bridge that NEAR token to that chain.

---

### Likelihood Explanation

- The `LogMetadataEvent` containing the MPC signature is emitted publicly on NEAR and is trivially observable by any party monitoring the chain.
- No privileged access is required; any account can call `deployToken` on any destination chain with the replayed signature and payload.
- The attack is a single transaction on the target chain and requires no capital.
- The window of opportunity is the time between NEAR emitting the `LogMetadataEvent` and the legitimate relayer submitting the `deployToken` transaction on the target chain — typically minutes to hours.

---

### Recommendation

Include the destination chain ID in the `MetadataPayload` borsh encoding on every chain, mirroring the pattern already used for `TransferMessagePayload`:

1. Add a `destination_chain: u8` field to `MetadataPayload` in `near/omni-types/src/lib.rs`.
2. Populate it in `log_metadata_callback` before signing.
3. Include it in the borsh encoding in `OmniBridge.sol` (`deployToken`), `starknet/src/bridge_types.cairo` (`MetadataPayload.to_borsh`), and `solana/.../deploy_token.rs` (`serialize_for_near`).

Alternatively, adopt a domain-separation prefix (contract address + chain ID) analogous to EIP-712, applied uniformly to all signed payloads.

---

### Proof of Concept

1. Monitor NEAR for a `LogMetadataEvent` for token `foo.near` targeting EVM chain A. Extract `(signature, metadata_payload)`.
2. On EVM chain B (a different EVM deployment of OmniBridge with a different `omniBridgeChainId` but the same `nearBridgeDerivedAddress`), call:
   ```solidity
   omniBridgeChainB.deployToken(signature, metadata_payload);
   ```
   This succeeds because the hash `keccak256(Metadata | "foo.near" | name | symbol | decimals)` is identical on both chains.
3. `nearToEthToken["foo.near"]` on chain B now points to the attacker-triggered proxy.
4. When the legitimate relayer later calls `deployToken` on chain B, it reverts with `ERR_TOKEN_EXIST`.
5. `foo.near` can never be legitimately bridged to chain B; all user funds destined for chain B for that token are permanently unclaimable.

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

**File:** near/omni-types/src/near_events.rs (L30-33)
```rust
    LogMetadataEvent {
        signature: SignatureResponse,
        metadata_payload: MetadataPayload,
    },
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L29-36)
```rust
        // 3. token
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.1.serialize(&mut writer)?;
```

**File:** starknet/src/omni_bridge.cairo (L207-209)
```text
            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```
