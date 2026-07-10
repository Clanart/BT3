### Title
Missing Chain ID in `deployToken` Signed Message Enables Cross-Chain Replay Attack - (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `deployToken` / `deploy_token` message hash across EVM, StarkNet, and Solana does **not** include a chain identifier, while the `finTransfer` / `fin_transfer` message hash on every chain **does** include `omniBridgeChainId`. A valid NEAR-MPC-signed `deployToken` signature obtained from one chain can be replayed verbatim on any other chain where the OmniBridge is deployed with the same `nearBridgeDerivedAddress`, deploying the token without NEAR's explicit per-chain authorization and permanently blocking legitimate future deployment on that chain.

---

### Finding Description

**EVM — `deployToken` (no chain ID):**

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)          // ← no omniBridgeChainId
);
bytes32 hashed = keccak256(borshEncoded);
``` [1](#0-0) 

**EVM — `finTransfer` (chain ID present, for comparison):**

```solidity
bytes1(omniBridgeChainId),   // token address chain
...
bytes1(omniBridgeChainId),   // recipient chain
``` [2](#0-1) 

**StarkNet — `MetadataPayload::to_borsh` (no chain ID):**

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);  // ← no chain_id
``` [3](#0-2) 

**StarkNet — `TransferMessagePayload::to_borsh` (chain ID present, for comparison):**

```cairo
borsh_bytes.append_byte(chain_id);   // token address chain
...
borsh_bytes.append_byte(chain_id);   // recipient chain
``` [4](#0-3) 

**Solana — `DeployTokenPayload::serialize_for_near` (no chain ID):**

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?;   // ← no SOLANA_OMNI_BRIDGE_CHAIN_ID
``` [5](#0-4) 

**Solana — `FinalizeTransferPayload::serialize_for_near` (chain ID present, for comparison):**

```rust
writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;  // token chain
...
writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;  // recipient chain
``` [6](#0-5) 

The `SignedPayload::verify_signature` on Solana and `_verify_borsh_signature` on StarkNet both hash only the serialized payload bytes — they have no additional domain separator that would distinguish chains. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

When the OmniBridge is deployed on multiple EVM chains (Ethereum, BSC, Polygon, Arbitrum, etc.) sharing the same `nearBridgeDerivedAddress`:

1. NEAR MPC signs a `deployToken` message for token `X` on Ethereum. The signature is publicly visible on-chain.
2. An attacker extracts the `(signatureData, metadata)` tuple and calls `deployToken` on BSC with the identical arguments.
3. The BSC contract verifies the signature successfully (same hash, same signer), sets `nearToEthToken[metadata.token]` to the newly deployed proxy, and marks `isBridgeToken[proxy] = true`.
4. When NEAR later legitimately tries to deploy token `X` on BSC, the call reverts with `ERR_TOKEN_EXIST` because `nearToEthToken[metadata.token]` is already populated.
5. There is no admin function to remove a bridge token deployed via `deployToken` (only `removeCustomToken` exists for custom tokens), so the mapping is **permanently** set.
6. Any user who initiates a transfer of token `X` from NEAR to BSC will have their NEAR-side funds locked with no path to completion, because NEAR cannot register a valid BSC token address for that token.

This matches the **Permanent freezing / irrecoverable lock** impact class and the **Cross-chain replay** impact class.

---

### Likelihood Explanation

- The attack requires no privileged access. Any on-chain observer can read a `deployToken` transaction on one chain and replay it on another.
- The OmniBridge is explicitly designed for multi-chain deployment (EVM, StarkNet, Solana all share the same NEAR MPC signer).
- The attacker only needs to act before NEAR deploys the token on the target chain — a race condition that is trivially won by monitoring the mempool or finalized blocks of the source chain.
- The inconsistency is systematic: every `deployToken` path (EVM, StarkNet, Solana) omits the chain ID, while every `finTransfer` path includes it, confirming this is a structural gap rather than an isolated oversight.

---

### Recommendation

Include `omniBridgeChainId` in the `deployToken` Borsh-encoded message hash on every chain, mirroring the pattern already used in `finTransfer`:

**EVM:**
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

**StarkNet (`MetadataPayload::to_borsh`):**
```cairo
borsh_bytes.append_byte(chain_id);   // ← add chain_id parameter
```

**Solana (`DeployTokenPayload::serialize_for_near`):**
```rust
writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;  // ← add this
```

The NEAR bridge's `sign_log_metadata` / metadata signing path must also include the destination chain ID in the payload it submits to the MPC signer, so the signed bytes match.

---

### Proof of Concept

**Setup:** OmniBridge deployed on Ethereum (`omniBridgeChainId = 1`) and BSC (`omniBridgeChainId = 2`), both configured with the same `nearBridgeDerivedAddress`.

**Steps:**

1. NEAR MPC signs a `deployToken` for `token = "wrap.near"`, `name = "Wrapped NEAR"`, `symbol = "wNEAR"`, `decimals = 24`. The Borsh-encoded hash is:
   ```
   keccak256( 0x01 || borsh("wrap.near") || borsh("Wrapped NEAR") || borsh("wNEAR") || 0x18 )
   ```
   This hash is **identical** on Ethereum and BSC because no chain ID is included.

2. The relayer calls `deployToken(sig, metadata)` on Ethereum. The transaction is publicly visible.

3. Attacker copies `sig` and `metadata` from the Ethereum transaction and calls `deployToken(sig, metadata)` on BSC. The signature check passes (same hash, same signer). A new `BridgeToken` proxy is deployed on BSC and `nearToEthToken["wrap.near"]` is set.

4. NEAR later attempts to deploy `wrap.near` on BSC. The NEAR bridge signs the same metadata payload (no chain ID → same bytes → same signature). The BSC contract reverts: `ERR_TOKEN_EXIST`.

5. All user transfers of `wrap.near` from NEAR to BSC are permanently uncompletable. Funds locked on NEAR cannot be released because no valid BSC token address can ever be registered for `wrap.near`.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L294-297)
```text
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
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

**File:** starknet/src/bridge_types.cairo (L67-70)
```text
        borsh_bytes.append_byte(chain_id);
        borsh_bytes.append(@borsh::encode_address(*self.token_address));
        borsh_bytes.append(@borsh::encode_u128(*self.amount));
        borsh_bytes.append_byte(chain_id);
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L30-35)
```rust
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
        params.0.serialize(&mut writer)?;
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. recipient
        writer.write_all(&[SOLANA_OMNI_BRIDGE_CHAIN_ID])?;
```

**File:** solana/programs/bridge_token_factory/src/state/message/mod.rs (L24-47)
```rust
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
