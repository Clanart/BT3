### Title
Cross-Chain Signature Replay for `deployToken` Due to Missing Destination Chain Identifier in Signed Hash - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`)

---

### Summary

The `MetadataPayload` hash signed by the MPC signer for `deployToken` / `deploy_token` does not include any destination chain identifier (chain ID or contract address). Because the same MPC-derived Ethereum address is used as the signer across EVM and StarkNet deployments, a valid `deployToken` signature obtained for one chain can be replayed verbatim on another chain to deploy a token without going through the proper `log_metadata` → MPC signing flow for that chain.

---

### Finding Description

The `finTransfer` / `fin_transfer` functions on both EVM and StarkNet correctly include `omniBridgeChainId` in the signed borsh payload, providing domain separation between chains. The `deployToken` / `deploy_token` functions do not.

**EVM `deployToken` hash construction** (`OmniBridge.sol:142-149`):
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
```
No chain ID. [1](#0-0) 

**EVM `finTransfer` hash construction** (for comparison, `OmniBridge.sol:289-309`):
```solidity
bytes1(omniBridgeChainId),   // ← chain ID present
Borsh.encodeAddress(payload.tokenAddress),
...
bytes1(omniBridgeChainId),   // ← chain ID present again
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

**StarkNet `MetadataPayload.to_borsh()`** (`bridge_types.cairo:36-44`):
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
No chain ID. [3](#0-2) 

**StarkNet `TransferMessagePayload.to_borsh(chain_id)`** (for comparison, `bridge_types.cairo:61-84`):
```cairo
borsh_bytes.append_byte(chain_id);  // ← chain ID present
``` [4](#0-3) 

**NEAR `log_metadata_callback`** also hashes `MetadataPayload` without any chain identifier:
```rust
let payload = near_sdk::env::keccak256_array(
    borsh::to_vec(&metadata_payload).near_expect(BridgeError::Borsh),
);
``` [5](#0-4) 

Both EVM and StarkNet verify the signature against the same MPC-derived Ethereum address (`nearBridgeDerivedAddress` / `omni_bridge_derived_address`) using the same Ethereum ECDSA scheme:

- EVM: `ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress` [6](#0-5) 
- StarkNet: `verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read())` [7](#0-6) 

Because the borsh encoding of `MetadataPayload` is identical on both chains (same field order, same borsh string encoding), the keccak256 hash is identical, and the same ECDSA signature is valid on both.

---

### Impact Explanation

An attacker who observes a valid `deployToken` transaction on one chain (e.g., Ethereum) can extract the signature and replay it on another chain (e.g., StarkNet) to:

1. Deploy a wrapped token on the target chain without going through the proper `log_metadata` → MPC signing flow for that chain.
2. Establish a token mapping (`near_to_starknet_token` / `nearToEthToken`) on the target chain prematurely or without protocol authorization.
3. Once deployed, the token mapping is permanent — a subsequent legitimate deployment attempt for the same token on that chain will fail with `ERR_TOKEN_EXIST` / `ERR_TOKEN_ALREADY_DEPLOYED`, permanently blocking the correct deployment path.

This constitutes an unauthorized token deployment via signature verification bypass, matching the allowed High impact: *"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, **token deployment**, or message execution."*

---

### Likelihood Explanation

- All `deployToken` transactions are public on-chain. Any observer can extract the `(signatureData, metadata)` pair.
- No special privileges are required to call `deployToken` / `deploy_token` on any chain.
- The attacker only needs to submit the extracted signature to the target chain's bridge contract.
- The attack is straightforward and requires no cryptographic capability.

---

### Recommendation

Include the destination chain identifier in the `MetadataPayload` hash, mirroring the pattern already used in `TransferMessagePayload`. Specifically:

**EVM** (`OmniBridge.sol`):
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // destination chain domain separator
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

**StarkNet** (`bridge_types.cairo`):
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
+   borsh_bytes.append_byte(chain_id);   // destination chain domain separator
    ...
}
```

**NEAR** (`lib.rs`): Include the destination chain kind in `MetadataPayload` before hashing.

The NEAR MPC signer must also include the destination chain in the payload it signs, so that a signature for chain A cannot be accepted by chain B.

---

### Proof of Concept

1. Observe a legitimate `deployToken(sig, {token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})` transaction on Ethereum mainnet. Extract `sig`.
2. Call `deploy_token(sig, MetadataPayload{token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})` on the StarkNet `OmniBridge` contract.
3. `_verify_borsh_signature` computes `keccak256(borsh([Metadata, "foo.near", "Foo", "FOO", 18]))` — identical to the Ethereum hash — and verifies against `omni_bridge_derived_address`. Verification passes.
4. A wrapped `FOO` token is deployed on StarkNet and registered in `near_to_starknet_token`, without any MPC signing event for StarkNet having occurred.
5. Any future legitimate attempt to deploy `foo.near` on StarkNet via the proper flow will revert with `ERR_TOKEN_ALREADY_DEPLOYED`.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L151-153)
```text
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

**File:** near/omni-bridge/src/lib.rs (L349-351)
```rust
        let payload = near_sdk::env::keccak256_array(
            borsh::to_vec(&metadata_payload).near_expect(BridgeError::Borsh),
        );
```

**File:** starknet/src/omni_bridge.cairo (L398-406)
```text
    fn _verify_borsh_signature(
        ref self: ContractState, borsh_bytes: @ByteArray, signature: Signature,
    ) {
        let message_hash_le = compute_keccak_byte_array(borsh_bytes);
        let message_hash = reverse_u256_bytes(message_hash_le);

        let sig = signature_from_vrs(signature.v, signature.r, signature.s);
        verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read());
    }
```
