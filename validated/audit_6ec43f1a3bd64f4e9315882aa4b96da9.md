Looking at the external report's vulnerability class — **cross-context signature replay due to missing domain separation** — I need to find where the Omni Bridge uses the same signed message format across different contexts without binding the signature to a specific context.

Let me trace the borsh-encoded signed messages for `deployToken` vs `finTransfer` across all three chains.

**EVM `OmniBridge.sol` — `deployToken`** (lines 142–149): encodes `[PayloadType::Metadata(0x01), token, name, symbol, decimals]` — **no chain ID**.

**EVM `OmniBridge.sol` — `finTransfer`** (lines 289–308): encodes `[PayloadType::TransferMessage(0x00), destinationNonce, originChain, originNonce, omniBridgeChainId, tokenAddress, amount, omniBridgeChainId, recipient, ...]` — **chain ID included twice**.

**Starknet `bridge_types.cairo` — `MetadataPayload::to_borsh()`** (lines 36–44): encodes `[PayloadType::Metadata(0x01), token, name, symbol, decimals]` — **no chain ID**.

**Starknet `bridge_types.cairo` — `TransferMessagePayload::to_borsh(chain_id)`** (lines 61–84): **chain ID included**.

**Solana `deploy_token.rs` — `DeployTokenPayload::serialize_for_near()`** (lines 19–27): encodes `[IncomingMessageType::Metadata(0x01), token, name, symbol, decimals]` — **no chain ID**.

**Solana `finalize_transfer.rs` — `FinalizeTransferPayload::serialize_for_near()`** (lines 20–43): **`SOLANA_OMNI_BRIDGE_CHAIN_ID` included**.

The asymmetry is clear and consistent across all three chains: `finTransfer` binds the signature to a specific chain via `omniBridgeChainId`/`SOLANA_OMNI_BRIDGE_CHAIN_ID`, but `deployToken` does not. All chains share the same `nearBridgeDerivedAddress` (derived from the NEAR MPC key). The borsh encoding for `deployToken` is byte-for-byte identical across EVM, Starknet, and Solana.

---

### Title
Cross-Chain Replay of `deployToken` Signature Enables Unauthorized Token Deployment on Any Bridge Chain — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

### Summary
The signed message for `deployToken`/`deploy_token` omits any destination chain identifier, while `finTransfer`/`fin_transfer` explicitly binds its signature to a chain via `omniBridgeChainId`. Because all bridge deployments share the same NEAR-derived signing key (`nearBridgeDerivedAddress`), a valid `deployToken` signature observed on one chain is cryptographically valid on every other chain. An unprivileged attacker can replay it to deploy bridge tokens on chains where NEAR has not authorized deployment, blocking legitimate deployments and potentially causing permanent user fund loss.

### Finding Description

**Root cause — missing chain binding in `deployToken` signed message:**

In `evm/src/omni-bridge/contracts/OmniBridge.sol`, `deployToken` constructs:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),  // 0x01
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [1](#0-0) 

No chain ID appears anywhere in this encoding.

In contrast, `finTransfer` on the same contract encodes `omniBridgeChainId` twice:

```solidity
bytes1(omniBridgeChainId),   // destination chain for token
...
bytes1(omniBridgeChainId),   // destination chain for recipient
``` [2](#0-1) 

The identical asymmetry exists in Starknet. `MetadataPayload::to_borsh()` produces `[0x01, token, name, symbol, decimals]` with no chain ID: [3](#0-2) 

While `TransferMessagePayload::to_borsh(chain_id)` explicitly receives and embeds `chain_id`: [4](#0-3) 

And in Solana, `DeployTokenPayload::serialize_for_near()` writes only `[IncomingMessageType::Metadata, token, name, symbol, decimals]`: [5](#0-4) 

While `FinalizeTransferPayload::serialize_for_near()` writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` into the message: [6](#0-5) 

**Why the same signature is valid on all chains:**

All bridge contracts verify against the same `nearBridgeDerivedAddress` / `omni_bridge_derived_address` — the Ethereum address derived from the NEAR MPC key. The signature verification in Starknet:

```cairo
fn _verify_borsh_signature(
    ref self: ContractState, borsh_bytes: @ByteArray, signature: Signature,
) {
    let message_hash_le = compute_keccak_byte_array(borsh_bytes);
    let message_hash = reverse_u256_bytes(message_hash_le);
    let sig = signature_from_vrs(signature.v, signature.r, signature.s);
    verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read());
}
``` [7](#0-6) 

And in Solana: [8](#0-7) 

All three use `keccak256(borsh_bytes)` verified against the same derived address. Since the borsh encoding of `deployToken` is identical across all chains (same type byte `0x01`, same string/u8 borsh layout), the signature is universally valid.

**Exploit flow:**

1. NEAR MPC signs a `deployToken` message for Ethereum: `keccak256([0x01, "token.near", "Token", "TKN", 18])` → `sig_A`.
2. The `deployToken(sig_A, payload)` call on Ethereum is publicly visible on-chain.
3. Attacker calls `deployToken(sig_A, payload)` on Arbitrum (or Base, or any other EVM chain, or Starknet, or Solana) before NEAR deploys the token there.
4. The signature passes verification because the message hash is identical and the signing key is the same.
5. The token is deployed on Arbitrum. `nearToEthToken["token.near"]` is now set on Arbitrum.
6. When NEAR's relayer later attempts to deploy the token on Arbitrum through the normal flow, it reverts with `"ERR_TOKEN_EXIST"`. [9](#0-8) 

### Impact Explanation

**Unauthorized token deployment (High):** An attacker can deploy any bridge token on any chain using a signature observed from any other chain, without NEAR's authorization for that specific chain. This bypasses the intended per-chain authorization model.

**Permanent fund lock (Critical path):** If the attacker deploys a token on chain B before NEAR has registered chain B's factory on the NEAR bridge, users who subsequently call `initTransfer` on chain B will have their tokens locked in the bridge contract. The NEAR bridge's `fin_transfer_callback` checks that the emitter address is a registered factory:

```rust
require!(
    self.factories
        .get(&init_transfer.emitter_address.get_chain())
        == Some(init_transfer.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
``` [10](#0-9) 

If the factory is not registered, the cross-chain transfer fails on NEAR and the tokens are irrecoverably locked in the EVM bridge contract (no refund path exists for failed NEAR-side finalization).

**Blocking legitimate deployments:** Even without the lock scenario, the attacker permanently occupies the `nearToEthToken` slot on the target chain, preventing NEAR from ever deploying the token there through the normal authorized flow.

### Likelihood Explanation

**High likelihood.** The attacker requires no privileged access. The only precondition is observing a valid `deployToken` transaction on any chain — these are public on-chain events. The attacker then submits the same calldata to any other chain's bridge contract. No MEV infrastructure or special tooling is needed. The Omni Bridge is deployed on multiple EVM chains (Ethereum, Arbitrum, Base, etc.) plus Starknet and Solana, giving many replay targets for every signature issued.

### Recommendation

Include the destination chain ID in the `deployToken` signed message, mirroring the pattern already used in `finTransfer`. For EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ADD: bind to this chain
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent change in `MetadataPayload::to_borsh()` in `starknet/src/bridge_types.cairo` and in `DeployTokenPayload::serialize_for_near()` in `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`. The NEAR MPC signer must then produce separate signatures per destination chain.

### Proof of Concept

```
Chain A = Ethereum (omniBridgeChainId = 1)
Chain B = Arbitrum  (omniBridgeChainId = 2)
Both share nearBridgeDerivedAddress = 0xABCD...

Step 1: NEAR MPC signs for Ethereum:
  msg = keccak256([0x01] ++ borsh("token.near") ++ borsh("Token") ++ borsh("TKN") ++ [0x12])
  sig = MPC_sign(msg)

Step 2: deployToken(sig, {token:"token.near", name:"Token", symbol:"TKN", decimals:18})
        is submitted on Ethereum → succeeds, token deployed at 0x1111...

Step 3: Attacker submits identical calldata to Arbitrum's OmniBridge:
  deployToken(sig, {token:"token.near", name:"Token", symbol:"TKN", decimals:18})

Step 4: Arbitrum OmniBridge computes:
  msg' = keccak256([0x01] ++ borsh("token.near") ++ borsh("Token") ++ borsh("TKN") ++ [0x12])
  msg' == msg  (no chain ID in encoding)
  ECDSA.recover(msg', sig) == nearBridgeDerivedAddress  ✓

Step 5: Token deployed on Arbitrum at 0x2222...
        nearToEthToken["token.near"] = 0x2222 on Arbitrum

Step 6: NEAR relayer attempts deployToken on Arbitrum → reverts "ERR_TOKEN_EXIST"
        NEAR cannot authorize the Arbitrum deployment through normal flow.

Step 7: Users bridge "token.near" from Arbitrum to NEAR.
        If Arbitrum factory not yet registered on NEAR → fin_transfer_callback fails
        → tokens permanently locked in Arbitrum bridge.
```

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

**File:** solana/programs/bridge_token_factory/src/state/message/mod.rs (L23-47)
```rust
impl<P: Payload> SignedPayload<P> {
    pub fn verify_signature(
        &self,
        params: P::AdditionalParams,
        derived_near_bridge_address: &[u8; 64],
    ) -> Result<()> {
        let serialized = self.payload.serialize_for_near(params)?;
        let hash = keccak::hash(&serialized);

        let signature_bytes = &self.signature[0..64];

        let signature = libsecp256k1::Signature::parse_standard_slice(signature_bytes)
            .map_err(|_| ProgramError::InvalidArgument)?;
        require!(!signature.s.is_high(), ErrorCode::MalleableSignature);

        let signer = secp256k1_recover(&hash.to_bytes(), self.signature[64], signature_bytes)
            .map_err(|_| error!(ErrorCode::SignatureVerificationFailed))?;

        require!(
            signer.0 == *derived_near_bridge_address,
            ErrorCode::SignatureVerificationFailed
        );

        Ok(())
    }
```

**File:** near/omni-bridge/src/lib.rs (L708-713)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```
