### Title
Missing Chain ID in `deployToken` Signature Hash Enables Cross-Chain Replay of Token Deployment — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`, `solana/programs/bridge_token_factory/src/state/message/deploy_token.rs`)

---

### Summary

The `deployToken` / `deploy_token` function on EVM, Starknet, and Solana constructs the signed message hash from the metadata payload (token ID, name, symbol, decimals) **without including any chain identifier**. Because the NEAR MPC derives a single key whose Ethereum address (`nearBridgeDerivedAddress`) is identical across all deployed chains, a valid NEAR-signed metadata signature for chain A is cryptographically indistinguishable from one for chain B. An unprivileged attacker can observe a legitimate `deployToken` transaction on one chain and replay it on any other chain, causing unauthorized token deployment and permanently corrupting the token mapping on the target chain.

---

### Finding Description

**EVM — `OmniBridge.sol` `deployToken`:**

The Borsh-encoded message that is hashed and verified contains only the payload type, token ID, name, symbol, and decimals. The `omniBridgeChainId` stored in the contract is **never included**:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)          // ← no omniBridgeChainId
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [1](#0-0) 

Contrast this with `finTransfer`, which correctly embeds `omniBridgeChainId` twice in the hash: [2](#0-1) 

**Starknet — `omni_bridge.cairo` `deploy_token`:**

`deploy_token` calls `_verify_borsh_signature` with `payload.to_borsh()`, which serializes only the metadata fields with no chain ID:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);  // ← no chain_id
    borsh_bytes
}
``` [3](#0-2) [4](#0-3) 

Again, `fin_transfer` correctly passes `self.omni_bridge_chain_id.read()` into `to_borsh(chain_id)`, but `deploy_token` does not: [5](#0-4) 

**Solana — `deploy_token.rs`:**

`DeployTokenPayload::serialize_for_near` serializes only the message type prefix and the payload fields. `SOLANA_OMNI_BRIDGE_CHAIN_ID` is used in `FinalizeTransferPayload` but is entirely absent from `DeployTokenPayload`:

```rust
fn serialize_for_near(&self, _params: Self::AdditionalParams) -> Result<Vec<u8>> {
    IncomingMessageType::Metadata.serialize(&mut writer)?;
    self.serialize(&mut writer)?;  // ← no SOLANA_OMNI_BRIDGE_CHAIN_ID
    ...
}
``` [6](#0-5) 

Compare with `FinalizeTransferPayload`, which writes `SOLANA_OMNI_BRIDGE_CHAIN_ID` for both token and recipient fields: [7](#0-6) 

**Root cause:** The NEAR MPC derives a single secp256k1 key pair. The resulting `nearBridgeDerivedAddress` / `omni_bridge_derived_address` / `derived_near_bridge_address` is the same Ethereum-style address on every chain. Because the `deployToken` hash is chain-agnostic, any valid signature produced by the NEAR MPC for deploying token X on chain A is also a valid signature for deploying token X on chain B, C, or D.

---

### Impact Explanation

An attacker who observes a legitimate `deployToken` transaction on chain A can immediately replay the identical `(signatureData, metadata)` tuple on chain B. The EVM bridge will:

1. Accept the signature (same hash, same signer address).
2. Deploy a new `ERC1967Proxy` token contract at a non-deterministic address (determined by the EVM's nonce at that moment, not by the token ID).
3. Write `nearToEthToken[metadata.token] = <attacker-triggered address>`.

After this, when NEAR legitimately attempts to deploy the same token on chain B, the call reverts with `"ERR_TOKEN_EXIST"` because `nearToEthToken[metadata.token]` is already set. The token mapping on chain B is permanently corrupted: it points to an address that NEAR never registered, so NEAR will never sign `finTransfer` payloads referencing that address. The bridge for that token on chain B is permanently broken — no user can ever bridge that token to chain B.

On Starknet the token address is deterministic (salt = `keccak(token_id).low`), so the address collision is exact, but the same permanent DoS on legitimate deployment applies. On Solana, the wrapped mint PDA is also deterministic, so the attacker's replay creates the mint before NEAR authorizes it.

**Impact category:** High — "Proof, signature, MPC verification bypass enabling unauthorized token deployment" and "token-mapping corruption that breaks bridge collateralization or misdirects value."

---

### Likelihood Explanation

- No privileged access is required. Any observer of on-chain transactions can extract the `(signatureData, metadata)` tuple from a confirmed `deployToken` call on chain A.
- The attacker only needs to submit the same calldata to the bridge contract on chain B before NEAR does so legitimately.
- The NEAR MPC signs metadata payloads as part of normal bridge operation; signatures are publicly visible on-chain.
- The attack is front-running-free on chains where NEAR has not yet deployed the token, and requires only a standard transaction submission.

---

### Recommendation

Include the destination chain identifier in the signed metadata hash for `deployToken`, mirroring the pattern already used in `finTransfer`. On EVM:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ← add chain binding
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Apply the equivalent fix to `MetadataPayloadTrait::to_borsh` on Starknet (pass and embed `omni_bridge_chain_id`) and to `DeployTokenPayload::serialize_for_near` on Solana (write `SOLANA_OMNI_BRIDGE_CHAIN_ID` before the payload). The NEAR MPC signing logic must be updated to include the destination chain ID when producing metadata signatures.

---

### Proof of Concept

1. NEAR MPC signs a `MetadataPayload` for `wrap.near` (name="Wrapped NEAR", symbol="wNEAR", decimals=24) to authorize deployment on Ethereum (`omniBridgeChainId = 0`). The resulting signature `sig` is broadcast in a `deployToken(sig, payload)` transaction on Ethereum.

2. Attacker observes the transaction and extracts `(sig, payload)`.

3. Attacker submits `deployToken(sig, payload)` to the Arbitrum OmniBridge (`omniBridgeChainId = 1`).

4. EVM verification: `keccak256(borshEncoded)` is identical on both chains (no chain ID in the hash). `ECDSA.recover` returns `nearBridgeDerivedAddress`. Signature check passes.

5. A new `ERC1967Proxy` for wNEAR is deployed on Arbitrum at address `0xABCD...` (determined by Arbitrum's current nonce). `nearToEthToken["wrap.near"] = 0xABCD...` is written.

6. NEAR later attempts to deploy wNEAR on Arbitrum. The call reverts: `"ERR_TOKEN_EXIST"`. NEAR never registered `0xABCD...`, so it will never sign `finTransfer` payloads for that address. The wNEAR bridge on Arbitrum is permanently broken.

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

**File:** starknet/src/omni_bridge.cairo (L202-205)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);
```

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
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
