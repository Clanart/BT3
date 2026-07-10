### Title
Missing Chain ID in `deployToken` Signed Payload Enables Cross-Chain Replay of Token Deployment Signatures - (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `deployToken`/`deploy_token` Borsh-encoded payload that the NEAR MPC signs does not include any chain-specific identifier. The identical encoding is accepted by all EVM chains, Starknet, and Solana. A single valid MPC signature obtained for deploying a token on one chain can be replayed on every other chain that shares the same `nearBridgeDerivedAddress`, deploying unauthorized bridge tokens and permanently blocking legitimate future deployments of the same token on those chains.

---

### Finding Description

The NEAR bridge's `log_metadata_callback` constructs and signs a `MetadataPayload` whose Borsh encoding is:

```
PayloadType::Metadata (0x01) | token_id | name | symbol | decimals
```

No chain ID, contract address, or any chain-specific field is included.

**NEAR side (signing):** [1](#0-0) 

**EVM `deployToken` verification** reconstructs the same encoding without any chain ID: [2](#0-1) 

**Starknet `deploy_token` verification** uses `MetadataPayloadImpl::to_borsh()` which produces the identical byte sequence: [3](#0-2) 

**Solana `deploy_token` verification** uses `DeployTokenPayload::serialize_for_near()` which also produces the same encoding: [4](#0-3) 

All three chains verify the signature against the same MPC-derived key (EVM and Starknet use the 20-byte Ethereum address; Solana uses the 64-byte uncompressed public key, which is the same underlying secp256k1 key). Because the signed bytes are identical across chains, a signature valid on one chain is cryptographically valid on all others.

**Contrast with `finTransfer`**, which correctly binds the payload to the destination chain by embedding `omniBridgeChainId` twice in the encoding: [5](#0-4) 

The Starknet CLAUDE.md explicitly documents this protection for `fin_transfer` but not for `deploy_token`: [6](#0-5) 

---

### Impact Explanation

An attacker who observes a valid `deployToken` transaction on Ethereum can immediately replay the same `(signatureData, metadata)` arguments on Arbitrum, Base, BNB, Polygon, HyperEvm, Abs, and Starknet. Each replay:

1. **Passes signature verification** — the MPC signature is over chain-agnostic bytes; `ECDSA.recover` / `verify_eth_signature` returns the same `nearBridgeDerivedAddress` on every chain.
2. **Deploys an unauthorized bridge token** — a new ERC20 proxy (or Starknet/Solana mint) is created and registered in the chain's `nearToEthToken` / `near_to_starknet_token` mapping with `isBridgeToken = true`.
3. **Permanently blocks legitimate deployment** — the `ERR_TOKEN_EXIST` guard prevents any future deployment of the same NEAR token ID on that chain: [7](#0-6) [8](#0-7) 

4. **Creates an orphaned bridge token** — the deployed token is a fully functional `BridgeToken` (mintable/burnable by the bridge) but the NEAR bridge has no record of it for that chain. Any `finTransfer` signed by NEAR for that chain+token would mint from this orphaned contract.

The net result is a permanent denial-of-service for legitimate token deployment on the replayed chains, and the creation of unbacked bridge tokens that the NEAR accounting layer does not track.

---

### Likelihood Explanation

The attack requires no privileged access. Any unprivileged observer can:
- Monitor any EVM chain for `deployToken` calldata (public mempool or confirmed transactions).
- Extract `signatureData` and `metadata` directly from the transaction.
- Submit the identical call to any other EVM chain's `OmniBridge` contract.

The attack is executable the moment a single legitimate `deployToken` transaction is confirmed anywhere. There is no time window or race condition to win — the replay succeeds on any chain where the token has not yet been deployed.

---

### Recommendation

Include the destination chain ID in the signed `MetadataPayload` encoding, mirroring the protection already present in `finTransfer`. On the NEAR signing side, add the target `ChainKind` (as a `u8`) to the `MetadataPayload` before hashing. On each destination chain, embed the locally-known chain ID into the Borsh encoding before verifying the signature. For example, on EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ← add this
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent change to `MetadataPayloadImpl::to_borsh()` in Starknet and `DeployTokenPayload::serialize_for_near()` in Solana, and update the NEAR `log_metadata_callback` to include the target chain kind in the signed payload.

---

### Proof of Concept

1. Alice calls `deployToken(sig, {token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})` on Ethereum mainnet. The transaction is confirmed.

2. Attacker Bob reads the calldata from the confirmed transaction. The `sig` is a valid NEAR MPC ECDSA signature over `keccak256(borsh(PayloadType::Metadata | "foo.near" | "Foo" | "FOO" | 18))`.

3. Bob submits the identical call to the Arbitrum `OmniBridge` (same `nearBridgeDerivedAddress`, same encoding, same signature). The call passes `ECDSA.recover(hashed, sig) == nearBridgeDerivedAddress` because the hash is chain-agnostic.

4. A new `BridgeToken` proxy for `foo.near` is deployed on Arbitrum. `isBridgeToken[proxy] = true`, `nearToEthToken["foo.near"] = proxy`.

5. When the NEAR bridge later attempts to legitimately deploy `foo.near` on Arbitrum, `deployToken` reverts with `ERR_TOKEN_EXIST`. The legitimate deployment is permanently blocked.

6. Bob repeats step 3 for Base, BNB, Polygon, HyperEvm, Abs, and Starknet using the same single signature.

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

**File:** starknet/CLAUDE.md (L45-45)
```markdown
1. **Chain ID binding**: Destination chain_id encoded in message hash (not in payload) - prevents cross-chain replay
```

**File:** starknet/src/omni_bridge.cairo (L207-209)
```text
            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```
