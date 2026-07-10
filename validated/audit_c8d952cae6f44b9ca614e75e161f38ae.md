### Title
Cross-Chain Replay of `deployToken` Metadata Signatures Due to Missing Chain ID Binding — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `MetadataPayload` signed by the NEAR MPC for `deployToken` / `deploy_token` does not include a destination chain ID in the signed message. Unlike `finTransfer` / `fin_transfer`, which explicitly bind the signed payload to a specific chain, the metadata signature is chain-agnostic. A valid signature obtained for deploying a token on one chain can be replayed verbatim on any other chain where the bridge is deployed, enabling unauthorized token deployment without NEAR's per-chain authorization.

---

### Finding Description

`finTransfer` on EVM encodes `omniBridgeChainId` twice into the Borsh payload before signing:

```solidity
bytes1(omniBridgeChainId),   // line 294
...
bytes1(omniBridgeChainId),   // line 297
```

`fin_transfer` on Starknet similarly passes `chain_id` into `to_borsh(chain_id)`:

```cairo
borsh_bytes.append_byte(chain_id);  // destination chain
...
borsh_bytes.append_byte(chain_id);  // recipient chain
```

The Starknet CLAUDE.md explicitly documents this as a security design decision: *"Chain ID binding: Destination chain_id encoded in message hash (not in payload) - prevents cross-chain replay."*

However, `deployToken` on EVM encodes **no chain ID**:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

`deploy_token` on Starknet's `MetadataPayload.to_borsh()` also encodes **no chain ID**:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
    borsh_bytes
}
```

And `DeployTokenPayload` on Solana also contains no chain ID:

```rust
pub struct DeployTokenPayload {
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
```

Because all chains share the same `nearBridgeDerivedAddress` / `derived_near_bridge_address` (the NEAR MPC key), a signature that passes verification on one chain passes on all others.

---

### Impact Explanation

**High — Proof/signature bypass enabling unauthorized token deployment.**

The most severe impact is on Starknet:

1. NEAR MPC signs a `MetadataPayload` for token `foo.near` to deploy on Ethereum. The signature is publicly observable (e.g., from the Ethereum transaction calldata or a relayer broadcast).
2. An attacker calls `deploy_token` on Starknet with the identical `(signature, payload)`.
3. Starknet's `_verify_borsh_signature` passes — the payload bytes are identical, the signer is the same NEAR MPC key.
4. `near_to_starknet_token[keccak("foo.near")]` is written to the attacker-triggered deployment address.
5. No Wormhole message is posted back to NEAR from Starknet's `deploy_token`, so NEAR never registers this Starknet deployment.
6. When NEAR later attempts the official deployment of `foo.near` on Starknet, it fails with `ERR_TOKEN_ALREADY_DEPLOYED`.
7. There is no `remove_token` or equivalent admin function on Starknet to clear the mapping (unlike EVM which has `removeToken`).
8. `foo.near` is permanently undeployable on Starknet via the official path, and since NEAR has no record of the Starknet address, users can never bridge `foo.near` to Starknet.

On EVM, the impact is partially mitigated by the `removeToken` admin function, but the same replay is possible across multiple EVM deployments (Ethereum, Polygon, Arbitrum, etc.) sharing the same `nearBridgeDerivedAddress`.

On Solana, `deploy_token` posts a Wormhole message back to NEAR, so NEAR would receive an unauthorized registration of the Solana mint, corrupting the NEAR-side token registry for that token.

---

### Likelihood Explanation

**Medium.** The `MetadataPayload` signature is submitted in a public transaction on the first chain it is used on. Any observer — including a MEV bot or a competing relayer — can extract the `(signatureData, metadata)` from that transaction and replay it on other chains before the official relayer does. The attacker needs no special access, only the ability to read public blockchain state and submit a transaction on another chain.

---

### Recommendation

Include the destination chain ID in the `MetadataPayload` Borsh encoding for all chains, mirroring the existing pattern used in `TransferMessagePayload`:

**EVM:**
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // add chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

**Starknet:**
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append_byte(chain_id);   // add chain binding
    ...
}
```

**Solana:**
```rust
pub struct DeployTokenPayload {
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
    pub destination_chain: u8,   // add chain binding
}
```

The NEAR MPC must include the destination chain ID when signing metadata payloads, and each chain's bridge contract must verify that the chain ID in the signed message matches its own `omniBridgeChainId`.

Additionally, add a `remove_token` admin function to the Starknet contract to allow recovery from erroneous deployments.

---

### Proof of Concept

1. NEAR MPC signs `MetadataPayload { token: "foo.near", name: "Foo", symbol: "FOO", decimals: 18 }` for Ethereum deployment. The Borsh encoding is `[0x01, len("foo.near"), "foo.near", len("Foo"), "Foo", len("FOO"), "FOO", 18]`. The resulting signature `sig` is submitted in an Ethereum `deployToken` transaction.

2. Attacker observes `sig` and the payload from the Ethereum transaction.

3. Attacker calls Starknet's `deploy_token(sig, MetadataPayload { token: "foo.near", name: "Foo", symbol: "FOO", decimals: 18 })`.

4. Starknet's `_verify_borsh_signature` computes `keccak(payload.to_borsh())` — identical bytes to what NEAR signed — and recovers the same `nearBridgeDerivedAddress`. Verification passes.

5. `deploy_syscall` deploys a new bridge token contract. `near_to_starknet_token[keccak("foo.near")] = new_address` is written.

6. No Wormhole message is posted. NEAR has no record of this Starknet deployment.

7. Official relayer later calls `deploy_token` on Starknet for `foo.near`. Transaction reverts: `ERR_TOKEN_ALREADY_DEPLOYED`.

8. `foo.near` is permanently unregistered on NEAR for Starknet. No bridging of `foo.near` to Starknet is possible. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** starknet/src/omni_bridge.cairo (L202-209)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

**File:** solana/programs/bridge_token_factory/src/state/message/deploy_token.rs (L9-27)
```rust
pub struct DeployTokenPayload {
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}

impl Payload for DeployTokenPayload {
    type AdditionalParams = ();

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
