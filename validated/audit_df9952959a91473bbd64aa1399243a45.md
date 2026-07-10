### Title
`MetadataPayload` Signature Lacks Chain Binding, Enabling Cross-Chain Replay to Permanently Block Token Deployment - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/bridge_types.cairo`, `near/omni-types/src/lib.rs`)

---

### Summary

The MPC-signed `MetadataPayload` used in `deployToken` / `deploy_token` across EVM and Starknet bridges does not include the destination chain ID or contract address in the signed message. A single valid signature produced by NEAR's MPC signer for one chain is therefore cryptographically valid on every other chain where the OmniBridge is deployed. An unprivileged attacker can replay this publicly-visible signature on any other deployment, permanently preventing the legitimate token from ever being deployed on that chain.

---

### Finding Description

**Root cause — signing side (`near/omni-types/src/lib.rs` + `near/omni-bridge/src/lib.rs`)**

`MetadataPayload` contains no chain identifier:

```rust
pub struct MetadataPayload {
    pub prefix: PayloadType,   // always Metadata = 1
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
```

`log_metadata_callback` serialises and signs exactly this struct with no chain context:

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
    ...
    .sign(SignRequest { payload, path: SIGN_PATH.to_owned(), key_version: 0 })
``` [1](#0-0) [2](#0-1) 

**Verification side — EVM (`evm/src/omni-bridge/contracts/OmniBridge.sol`)**

`deployToken` reconstructs the hash from `{PayloadType.Metadata, token, name, symbol, decimals}` — no `omniBridgeChainId`:

```solidity
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
``` [3](#0-2) 

**Verification side — Starknet (`starknet/src/bridge_types.cairo`)**

`MetadataPayload.to_borsh()` likewise omits the chain ID:

```cairo
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    let mut borsh_bytes: ByteArray = Default::default();
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);
    borsh_bytes
}
``` [4](#0-3) 

**Contrast with `finTransfer` / `fin_transfer` — which ARE protected**

`finTransfer` on EVM embeds `omniBridgeChainId` twice (for token chain and recipient chain):

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [5](#0-4) 

`fin_transfer` on Starknet passes `chain_id` into `to_borsh`:

```cairo
_verify_borsh_signature(
    ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
);
``` [6](#0-5) 

The `MetadataPayload` path is the only signed message type that omits this binding.

---

### Impact Explanation

An attacker who replays a valid `deployToken` signature on a second chain causes:

1. The bridge token for that NEAR token ID is deployed at an attacker-triggered address on the target chain.
2. The target chain's `nearToEthToken` / `near_to_starknet_token` mapping is permanently set to that address.
3. Any subsequent legitimate `deployToken` call for the same token on that chain reverts with `ERR_TOKEN_EXIST` (EVM) or `ERR_TOKEN_ALREADY_DEPLOYED` (Starknet).
4. NEAR's `deploy_token_callback` can never succeed for that chain, so NEAR's `token_id_to_address` mapping for that chain is never populated.
5. `sign_transfer` cannot resolve a token address for that chain and panics with `FailedToGetTokenAddress`.
6. **Result: the token is permanently un-bridgeable to that chain.** Any user funds already locked on the origin chain for a transfer to that chain cannot be settled — a permanent freeze matching the allowed impact class. [7](#0-6) [8](#0-7) 

---

### Likelihood Explanation

- The MPC signature is produced in a NEAR transaction (`sign_log_metadata_callback`) that is publicly visible on-chain the moment it is finalised.
- The attacker needs no special role, no tokens, and no privileged access — only the ability to call `deployToken` on a second EVM chain or `deploy_token` on Starknet with the replayed `signatureData`.
- The OmniBridge is deployed on multiple EVM chains (Ethereum, Arbitrum, Base, etc.) sharing the same `nearBridgeDerivedAddress`, so every new token deployment signature is immediately replayable across all of them.
- The attack window opens the moment the NEAR MPC signature is finalised and closes only when the legitimate `deployToken` is mined on the target chain — a race that a monitoring bot wins reliably.

---

### Recommendation

Include the destination chain ID (and optionally the bridge contract address) in the `MetadataPayload` before signing, mirroring the pattern already used in `TransferMessagePayload`:

**NEAR types (`near/omni-types/src/lib.rs`):**
```rust
pub struct MetadataPayload {
    pub prefix: PayloadType,
    pub destination_chain: ChainKind,   // ADD
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
```

**NEAR signing (`near/omni-bridge/src/lib.rs` `log_metadata_callback`):**
Pass the intended destination chain when constructing `MetadataPayload`.

**EVM verification (`OmniBridge.sol` `deployToken`):**
```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ADD
    Borsh.encodeString(metadata.token),
    ...
);
```

**Starknet verification (`bridge_types.cairo` `MetadataPayload.to_borsh`):**
```cairo
fn to_borsh(self: @MetadataPayload, chain_id: u8) -> ByteArray {
    borsh_bytes.append_byte(chain_id);  // ADD
    ...
}
```

---

### Proof of Concept

1. NEAR operator calls `log_metadata("usdc.near")`. NEAR's `log_metadata_callback` constructs `MetadataPayload{prefix:1, token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6}`, hashes it, and requests an MPC signature. The resulting `(r, s, v)` signature appears in the NEAR transaction receipt.

2. Attacker observes the signature. The OmniBridge is deployed on both Ethereum (`omniBridgeChainId = 1`) and Arbitrum (`omniBridgeChainId = 2`).

3. Legitimate relayer calls `deployToken(sig, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on Ethereum — succeeds, USDC bridge token deployed at `addr_eth`.

4. Attacker immediately calls `deployToken(sig, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on Arbitrum with the **same** `sig`. Because the hash does not include `omniBridgeChainId`, `ECDSA.recover` returns `nearBridgeDerivedAddress` and the check passes. USDC bridge token is deployed at `addr_arb_attacker`.

5. When the legitimate relayer later calls `deployToken` on Arbitrum, it reverts: `require(!isBridgeToken[nearToEthToken["usdc.near"]], "ERR_TOKEN_EXIST")`.

6. NEAR's `deploy_token_callback` for Arbitrum never succeeds; `token_id_to_address[(Arb, "usdc.near")]` is never set. Any user who initiates a NEAR→Arbitrum USDC transfer has their funds locked in NEAR's bridge with no path to settlement. [9](#0-8) [3](#0-2) [4](#0-3) [10](#0-9)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L341-360)
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
```

**File:** near/omni-bridge/src/lib.rs (L462-469)
```rust
        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L293-298)
```text
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

**File:** starknet/src/omni_bridge.cairo (L202-209)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

            let token_id_hash = compute_keccak_byte_array(@payload.token);
            let existing_token = self.near_to_starknet_token.read(token_id_hash);
            assert(existing_token.is_zero(), 'ERR_TOKEN_ALREADY_DEPLOYED');
```

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
```
