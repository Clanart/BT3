### Title
Cross-Chain Replay of `deployToken`/`deploy_token` Signature Due to Missing Chain ID in Metadata Payload — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`)

### Summary

The `MetadataPayload` borsh encoding used for signature verification in `deployToken` (EVM) and `deploy_token` (Starknet) omits any chain identifier. Because NEAR signs the same chain-agnostic payload for all destination chains using a single MPC key path, a signature that is publicly observable from one chain's deployment event can be replayed verbatim on any other chain that shares the same `nearBridgeDerivedAddress`, deploying an unauthorized bridge token without NEAR's per-chain authorization.

---

### Finding Description

**Root cause — missing chain ID in the metadata payload hash**

On the EVM side, `deployToken` constructs its signed message as:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
``` [1](#0-0) 

No `omniBridgeChainId` appears anywhere in this hash. Compare this with `finTransfer`, which explicitly binds the message to the destination chain:

```solidity
bytes1(omniBridgeChainId),   // destination chain — present in finTransfer
Borsh.encodeAddress(payload.tokenAddress),
``` [2](#0-1) 

On the Starknet side, `MetadataPayload.to_borsh()` is identically chain-agnostic:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
    borsh_bytes
}
``` [3](#0-2) 

Again, contrast with `TransferMessagePayload.to_borsh(chain_id: u8)`, which does bind to the chain: [4](#0-3) 

**NEAR signing — same payload, same key path, for all chains**

On the NEAR side, `log_metadata_callback` constructs and signs a `MetadataPayload` that contains only `token`, `name`, `symbol`, and `decimals` — no chain identifier — and submits it to the MPC signer using the fixed constant `SIGN_PATH`:

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
    .sign(SignRequest { payload, path: SIGN_PATH.to_owned(), key_version: 0 })
``` [5](#0-4) 

Because `SIGN_PATH` is a single constant, the MPC contract derives the same secp256k1 key for every chain. The resulting `nearBridgeDerivedAddress` / `omni_bridge_derived_address` is therefore identical across EVM and Starknet deployments.

**Signature is publicly observable**

The signed metadata is emitted as a NEAR event:

```rust
OmniBridgeEvent::LogMetadataEvent { signature, metadata_payload }
``` [6](#0-5) 

Any observer can extract the `(signature, metadata_payload)` pair from the NEAR chain.

**Replay path**

Both chains verify the signature against the same keccak256 of the same borsh bytes:
- EVM: `ECDSA.recover(keccak256(borshEncoded), signatureData) == nearBridgeDerivedAddress`
- Starknet: `verify_eth_signature(reverse_u256_bytes(compute_keccak_byte_array(borsh_bytes)), sig, omni_bridge_derived_address)` — the byte-reversal converts Cairo's little-endian keccak output to the same big-endian value EVM produces. [7](#0-6) 

Because the borsh encoding is identical and the hash is identical, a signature that passes EVM verification also passes Starknet verification, and vice versa.

---

### Impact Explanation

**High — Signature/MPC verification bypass enabling unauthorized token deployment.**

An attacker who observes a `LogMetadataEvent` on NEAR (e.g., for a token being deployed on Ethereum) can immediately submit the same `(signature, MetadataPayload)` to the Starknet `deploy_token` entry point (or to any other EVM chain's `deployToken`). This:

1. Deploys a bridge token on a chain that NEAR never authorized for that token.
2. Registers the token in `near_to_starknet_token` / `nearToEthToken`, permanently blocking any future legitimate deployment of that token on that chain (the `ERR_TOKEN_ALREADY_DEPLOYED` / `ERR_TOKEN_EXIST` guard fires on the next legitimate attempt).
3. If the chain later becomes a supported destination, the already-registered token address is the one that will receive `finTransfer` mints — but the token was deployed by the attacker's replay, not by the authorized flow, potentially with a manipulated `name`/`symbol`/`decimals` if the attacker crafted the payload.

---

### Likelihood Explanation

**Medium.** The signature is unconditionally emitted as a public NEAR event. No privileged access, leaked key, or colluding party is required. The attacker only needs to monitor NEAR events and submit a transaction to the target chain. The only friction is that the attacker must act before the legitimate deployer does on the target chain.

---

### Recommendation

Bind the metadata payload to the destination chain by including the chain ID in the borsh-encoded message that is signed and verified. Mirror the pattern already used in `finTransfer`/`fin_transfer`:

**EVM (`deployToken`):**
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // add destination chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

**Starknet (`MetadataPayload.to_borsh`):**
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
+   borsh_bytes.append_byte(chain_id);   // add destination chain binding
    ...
}
```

**NEAR (`log_metadata_callback`):** Pass the target chain ID into `MetadataPayload` (or a wrapper) before hashing and signing, so each chain receives a distinct signature.

---

### Proof of Concept

1. NEAR operator calls `log_metadata("token.near")` targeting Ethereum (chain ID `0x02`).
2. NEAR emits `LogMetadataEvent { signature: S, metadata_payload: P }` — publicly visible on-chain.
3. Attacker reads `S` and `P` from the NEAR event.
4. Attacker calls Starknet `deploy_token(S, P)`.
5. Starknet computes `keccak256(borsh(P))` — identical to what Ethereum computed, because no chain ID is in `P`.
6. `verify_eth_signature` passes because `S` was produced over the same hash.
7. A bridge token for `token.near` is now deployed on Starknet without NEAR's authorization.
8. When NEAR later legitimately tries to deploy `token.near` on Starknet, the call reverts with `ERR_TOKEN_ALREADY_DEPLOYED`, permanently blocking the legitimate deployment.

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

**File:** near/omni-bridge/src/lib.rs (L341-365)
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
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SIGN_LOG_METADATA_CALLBACK_GAS)
                    .sign_log_metadata_callback(metadata_payload),
            )
```

**File:** near/omni-types/src/near_events.rs (L30-33)
```rust
    LogMetadataEvent {
        signature: SignatureResponse,
        metadata_payload: MetadataPayload,
    },
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
