### Title
`deployToken` MPC Signature Lacks Chain ID, Enabling Cross-Chain Replay of Token Deployment - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

The `deployToken` function in `OmniBridge.sol` verifies an MPC-produced ECDSA signature over a Borsh-encoded `MetadataPayload` that contains no chain identifier. Because the same MPC key (`nearBridgeDerivedAddress`) is shared across all EVM deployments of the bridge, a valid `deployToken` signature obtained for one EVM chain (e.g., Ethereum) can be replayed verbatim on any other EVM chain (e.g., Arbitrum, Base, Optimism) to deploy a bridge token without the bridge operator's authorization. This is the direct analog of the LifeBuoy domain-separation failure: just as LifeBuoy's deployer-identity check does not bind to a specific chain, the `deployToken` signature does not bind to a specific destination chain.

### Finding Description

In `OmniBridge.sol`, `deployToken` constructs the signed payload as:

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
``` [1](#0-0) 

`omniBridgeChainId` — the per-chain identifier stored in contract state — is entirely absent from this payload. Contrast this with `finTransfer`, which correctly embeds `omniBridgeChainId` twice (once for the token address context and once for the recipient address context):

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

The NEAR-side `MetadataPayload` that the MPC signs is equally chain-agnostic:

```rust
let metadata_payload = MetadataPayload {
    prefix: PayloadType::Metadata,
    token: token_id.to_string(),
    name: metadata.name,
    symbol: metadata.symbol,
    decimals: metadata.decimals,
};
``` [3](#0-2) 

No chain kind, no `omniBridgeChainId`, no contract address is included. The signed bytes are therefore identical regardless of which EVM chain the deployment targets.

Once `deployToken` succeeds, the bridge permanently records the mapping:

```solidity
isBridgeToken[address(bridgeTokenProxy)] = true;
ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
nearToEthToken[metadata.token] = address(bridgeTokenProxy);
``` [4](#0-3) 

There is no `removeToken` or equivalent function in `OmniBridge.sol`, so once the attacker-triggered mapping is written it cannot be deleted through normal protocol operations.

### Impact Explanation

**Unauthorized token deployment and permanent token-mapping corruption across EVM chains.**

1. An attacker replays a `deployToken` signature from chain A on chain B. The signature check passes because the payload is chain-agnostic.
2. A `BridgeToken` proxy is deployed on chain B at an address determined by the current nonce of the chain-B `OmniBridge` contract — an address the bridge operator did not choose or anticipate.
3. The `nearToEthToken` mapping on chain B is permanently set to this attacker-triggered address. Because there is no removal function, the bridge operator cannot overwrite it via `deployToken` (the `ERR_TOKEN_EXIST` guard blocks re-deployment).
4. If the operator intended to register a native/custom token on chain B via `addCustomToken` (e.g., native USDC), the attacker's prior deployment forces the `nearToEthToken` slot to point to a wrapped token instead. While `addCustomToken` can overwrite `nearToEthToken`, the orphaned wrapped-token entry remains marked `isBridgeToken[wrappedProxy] = true`, creating a permanently inconsistent state.
5. The `OmniBridgeWormhole` extension publishes a `DeployToken` Wormhole message containing the attacker-chosen proxy address, which the NEAR bridge's `bind_token` callback then ingests as the canonical address for that chain — propagating the corruption to the NEAR-side token registry.

This matches the allowed High impact: **signature bypass enabling unauthorized token deployment**, and **token-mapping corruption that misdirects value**.

### Likelihood Explanation

The attack requires only:
- Observing any on-chain `deployToken` transaction on one EVM chain (fully public).
- Submitting the same `signatureData` + `metadata` calldata to `deployToken` on any other EVM chain where the same NEAR token has not yet been deployed.

An attacker can monitor all supported EVM chains and front-run every new token deployment. No privileged access, no key compromise, and no MPC collusion is required. The MPC key is the same across chains by design, so the replayed signature is always valid.

### Recommendation

Include `omniBridgeChainId` in the Borsh-encoded payload that the MPC signs for `deployToken`, mirroring the pattern already used in `finTransfer`. The NEAR-side `MetadataPayload` struct and the `log_metadata_callback` signing logic must be updated in parallel to include the destination chain identifier, so that a signature produced for Ethereum is cryptographically invalid on Arbitrum.

### Proof of Concept

1. Bridge operator calls `deployToken(sig_eth, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on Ethereum. Transaction is mined; `sig_eth` is now public.
2. Attacker calls `deployToken(sig_eth, {token:"usdc.near", name:"USD Coin", symbol:"USDC", decimals:6})` on Arbitrum.
3. `ECDSA.recover(keccak256(borshEncoded), sig_eth) == nearBridgeDerivedAddress` — passes, because the payload is identical on both chains.
4. A `BridgeToken` proxy is deployed on Arbitrum at address `0xDEF` (nonce-determined). `nearToEthToken["usdc.near"] = 0xDEF` is written permanently.
5. The Wormhole `DeployToken` message is published with `0xDEF`. NEAR's `bind_token` maps `(Arbitrum, "usdc.near") → 0xDEF`.
6. The bridge operator's planned `addCustomToken` call to register native Arbitrum USDC at `0xNATIVE` now conflicts: `nearToEthToken["usdc.near"]` already points to `0xDEF`, and the orphaned `isBridgeToken[0xDEF] = true` entry cannot be cleaned up.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L190-192)
```text
        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);
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

**File:** near/omni-bridge/src/lib.rs (L341-348)
```rust
        let metadata_payload = MetadataPayload {
            prefix: PayloadType::Metadata,
            token: token_id.to_string(),
            name: metadata.name,
            symbol: metadata.symbol,
            decimals: metadata.decimals,
        };

```
