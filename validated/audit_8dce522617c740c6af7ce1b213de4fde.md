### Title
Missing Chain ID in `deployToken` Metadata Signature Enables Cross-Chain Replay — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `deployToken` function on every EVM chain (and on StarkNet) verifies an MPC-signed metadata payload that contains **no destination chain identifier**. Because the same MPC signing path (`SIGN_PATH`) is used for all chains, the resulting `nearBridgeDerivedAddress` is identical across every deployment. A single valid `deployToken` signature obtained from one chain can therefore be replayed verbatim on every other supported EVM chain and on StarkNet, deploying an unauthorized bridge-token mapping without any new MPC authorization.

---

### Finding Description

**NEAR signing side — `log_metadata_callback`** constructs and signs a `MetadataPayload` that contains only `{ prefix, token, name, symbol, decimals }`:

```rust
// near/omni-bridge/src/lib.rs  lines 341-360
let metadata_payload = MetadataPayload {
    prefix: PayloadType::Metadata,
    token: token_id.to_string(),
    name: metadata.name,
    symbol: metadata.symbol,
    decimals: metadata.decimals,   // ← no chain ID anywhere
};
let payload = near_sdk::env::keccak256_array(
    borsh::to_vec(&metadata_payload)…
);
ext_signer::ext(self.mpc_signer.clone())
    .sign(SignRequest { payload, path: SIGN_PATH.to_owned(), key_version: 0 })
``` [1](#0-0) 

The same constant `SIGN_PATH` is used for every chain, so the MPC always derives the same Ethereum address (`nearBridgeDerivedAddress`).

**EVM verification side — `deployToken`** reconstructs the hash from the caller-supplied payload and checks it against `nearBridgeDerivedAddress`:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol  lines 142-153
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)          // ← no chain ID anywhere
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress)
    revert InvalidSignature();
``` [2](#0-1) 

**StarkNet verification side — `deploy_token`** has the identical gap; `MetadataPayload.to_borsh()` encodes only `{ PayloadType::Metadata, token, name, symbol, decimals }`:

```cairo
// starknet/src/bridge_types.cairo  lines 36-44
fn to_borsh(self: @MetadataPayload) -> ByteArray {
    borsh_bytes.append_byte(PayloadType::Metadata.into());
    borsh_bytes.append(@borsh::encode_byte_array(self.token));
    borsh_bytes.append(@borsh::encode_byte_array(self.name));
    borsh_bytes.append(@borsh::encode_byte_array(self.symbol));
    borsh_bytes.append_byte(*self.decimals);   // ← no chain ID
    borsh_bytes
}
``` [3](#0-2) 

**Contrast with `finTransfer`**, which correctly binds the message to the destination chain by embedding `omniBridgeChainId` twice:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol  lines 289-308
bytes1(omniBridgeChainId),          // before tokenAddress
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),          // before recipient
Borsh.encodeAddress(payload.recipient),
``` [4](#0-3) 

The `MetadataPayload` struct on the NEAR types side confirms the absence of any chain field:

```rust
// near/omni-types/src/lib.rs  lines 694-702
pub struct MetadataPayload {
    pub prefix: PayloadType,
    pub token: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,   // ← no chain ID
}
``` [5](#0-4) 

---

### Impact Explanation

An attacker who observes a legitimate `deployToken` call on Ethereum can immediately replay the identical `(signatureData, metadata)` tuple on Arbitrum, Base, BNB Chain, Polygon, HyperEVM, Abstract, and StarkNet. Each replay:

1. Passes the `ECDSA.recover` / `verify_eth_signature` check because the hash is chain-agnostic and `nearBridgeDerivedAddress` is the same everywhere.
2. Deploys a new `BridgeToken` proxy and writes `nearToEthToken[metadata.token]` on the target chain.
3. Permanently blocks the protocol from ever officially deploying that token on the target chain (the `ERR_TOKEN_EXIST` guard fires on any subsequent attempt).
4. If the attacker also submits a `bind_token` proof to NEAR using the `DeployToken` event emitted by the replay, the NEAR bridge registers the attacker-triggered token address as the canonical address for that chain, forcing all future NEAR→chain transfers to route through it.

This is a **signature/MPC verification bypass enabling unauthorized token deployment** — explicitly listed as a High-severity impact in the bounty scope.

---

### Likelihood Explanation

- No special privilege is required; any unprivileged observer of a public Ethereum transaction can extract `signatureData` and `metadata` and replay them.
- The attack is cheap (one transaction per target chain) and fully deterministic.
- The window is permanent: once a valid `deployToken` signature exists on-chain for any EVM deployment, it is replayable on all other chains indefinitely.

---

### Recommendation

Include the destination chain ID in the signed metadata payload, mirroring the pattern already used in `finTransfer`. On the NEAR side, add a `destination_chain: ChainKind` field to `MetadataPayload` and pass it through `log_metadata_callback`. On every destination chain, prepend the local `omniBridgeChainId` byte to the Borsh-encoded message before hashing. This makes each signature chain-specific and non-replayable.

---

### Proof of Concept

1. A relayer calls `deployToken(sig, {token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})` on Ethereum mainnet. The transaction is public.
2. Attacker copies `sig` and the `metadata` struct verbatim.
3. Attacker calls `deployToken(sig, {token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})` on the Arbitrum OmniBridge.
4. `ECDSA.recover(keccak256(borshEncoded), sig)` returns the same `nearBridgeDerivedAddress` (identical hash, identical key).
5. A new `BridgeToken` proxy is deployed on Arbitrum; `nearToEthToken["foo.near"]` is set to the attacker-triggered address.
6. Any subsequent legitimate attempt to deploy `foo.near` on Arbitrum reverts with `ERR_TOKEN_EXIST`.
7. Attacker submits the Arbitrum `DeployToken` event proof to NEAR's `bind_token`, registering the attacker-triggered address as the canonical Arbitrum address for `foo.near` in the NEAR bridge state.

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-308)
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
