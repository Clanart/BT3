### Title
Cross-Chain Replay of `MetadataPayload` MPC Signature Enables Unauthorized Token Deployment on Any Chain — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`)

---

### Summary

The MPC-signed `MetadataPayload` used in `deployToken` does not include any chain-specific domain separator (chain ID or contract address). Because the same MPC key is shared across all chains via NEAR chain signatures, a valid `deployToken` signature obtained for one chain (e.g., EVM Ethereum) can be replayed verbatim on any other chain (e.g., StarkNet, another EVM chain) to deploy the same token there without going through the proper `log_metadata` → MPC signing flow for that chain.

---

### Finding Description

The `MetadataPayload` borsh-encoded hash that is submitted to the MPC signer and later verified on destination chains is constructed identically on every chain:

**EVM (`OmniBridge.sol`, lines 142–148):**
```
[PayloadType::Metadata (1 byte)] + [token] + [name] + [symbol] + [decimals]
``` [1](#0-0) 

**StarkNet (`bridge_types.cairo`, lines 36–44):**
```
[PayloadType::Metadata (1 byte)] + [token] + [name] + [symbol] + [decimals]
``` [2](#0-1) 

**NEAR (`near/omni-types/src/lib.rs`, lines 694–702):**
`MetadataPayload` struct fields: `prefix`, `token`, `name`, `symbol`, `decimals` — no chain field. [3](#0-2) 

The byte sequences produced on EVM and StarkNet for the same token are **byte-for-byte identical**. The MPC signer signs the keccak256 of this sequence. Since the MPC key is a single key derived from NEAR chain signatures and shared across all chains, the resulting ECDSA signature is valid on every chain simultaneously.

Contrast this with `TransferMessagePayload`, which **correctly** includes `omniBridgeChainId` in the hash on EVM: [4](#0-3) 

And `chain_id` on StarkNet: [5](#0-4) 

The `MetadataPayload` signing path in NEAR's `log_metadata_callback` also omits any chain context: [6](#0-5) 

---

### Impact Explanation

An attacker who observes a valid `deployToken(signatureData, metadata)` call on chain A (e.g., EVM Ethereum) can immediately replay the same `signatureData` and `metadata` arguments on chain B (e.g., StarkNet) to:

1. **Deploy a bridge token on chain B without authorization** — bypassing the requirement that `log_metadata` be called on chain B and the MPC sign a payload for chain B.
2. **Front-run legitimate token deployments** — if the attacker replays before the legitimate relayer, the token is registered in chain B's bridge mapping under the attacker-triggered deployment. The legitimate deployment then reverts (`ERR_TOKEN_ALREADY_DEPLOYED` / `ERR_TOKEN_EXIST`), permanently blocking the official deployment flow for that token on that chain.
3. **Corrupt the bridge's cross-chain token mapping** — the NEAR bridge's `factories` and token address mappings for chain B would reference a token deployed via an unauthorized path, breaking collateralization accounting for that token on chain B.

This matches the allowed impact: **High — MPC signature verification bypass enabling unauthorized token deployment**.

---

### Likelihood Explanation

- All `deployToken` transactions are public on-chain; any observer can extract `signatureData` and `metadata` from a confirmed transaction on chain A.
- No privileged access is required; the attacker only needs to submit a transaction on chain B.
- The attack is straightforward: copy calldata from chain A's transaction and submit it to chain B's bridge contract.
- The window is open indefinitely — the signature never expires and there is no nonce in `MetadataPayload`.

---

### Recommendation

Include a chain-specific domain separator in the `MetadataPayload` hash, consistent with how `TransferMessagePayload` already handles this. Specifically:

- Add the destination chain's `omniBridgeChainId` byte to the borsh-encoded `MetadataPayload` before hashing, both on the NEAR signing side (`log_metadata_callback`) and on each destination chain's verification side (`deployToken`).
- On NEAR, the `MetadataPayload` struct should carry a `destination_chain: ChainKind` field that is included in the borsh serialization and passed to the MPC signer.
- On EVM and StarkNet, the verification must prepend/append `omniBridgeChainId` / `chain_id` to the encoded payload before hashing, matching the NEAR-side encoding.

---

### Proof of Concept

1. Legitimate relayer calls `log_metadata(token_id)` on NEAR for token `"wrap.near"`. MPC signs `keccak256(borsh([Metadata, "wrap.near", "Wrapped NEAR", "wNEAR", 24]))` → `sig`.
2. Relayer calls `deployToken(sig, {token:"wrap.near", name:"Wrapped NEAR", symbol:"wNEAR", decimals:24})` on EVM Ethereum. Token deployed at `0xAAA...`.
3. Attacker observes the EVM transaction, extracts `sig` and the `metadata` struct.
4. Attacker calls `deployToken(sig, {token:"wrap.near", name:"Wrapped NEAR", symbol:"wNEAR", decimals:24})` on StarkNet bridge.
5. StarkNet bridge computes `keccak256(borsh([Metadata, "wrap.near", "Wrapped NEAR", "wNEAR", 24]))` — identical bytes — recovers the same MPC-derived address, passes signature verification, and deploys the token on StarkNet.
6. When the legitimate relayer later attempts to deploy `"wrap.near"` on StarkNet, the call reverts with `ERR_TOKEN_ALREADY_DEPLOYED`, permanently blocking the official deployment.

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

**File:** near/omni-bridge/src/lib.rs (L341-366)
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
    }
```
