### Title
Cross-chain Replay of `deployToken` Signatures Enables Unauthorized Token Deployment on Unintended EVM Chains - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

The `deployToken` function in `OmniBridge.sol` verifies a NEAR-signed ECDSA signature but omits the destination chain ID (`omniBridgeChainId`) from the signed payload. Because `nearBridgeDerivedAddress` is derived from the same NEAR account and is identical across all EVM deployments, a valid `deployToken` signature obtained from one chain can be replayed verbatim on any other EVM chain where the OmniBridge is deployed, causing unauthorized bridge-token deployments without NEAR's per-chain authorization.

---

### Finding Description

In `OmniBridge.deployToken()`, the signed payload is constructed as:

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

The payload contains only `PayloadType.Metadata + token + name + symbol + decimals`. It does **not** include `omniBridgeChainId`.

Compare this with `finTransfer`, which correctly binds the signature to the destination chain:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
    Borsh.encodeUint64(payload.destinationNonce),
    bytes1(payload.originChain),
    Borsh.encodeUint64(payload.originNonce),
    bytes1(omniBridgeChainId),          // ← chain binding present
    Borsh.encodeAddress(payload.tokenAddress),
    ...
);
``` [2](#0-1) 

The `deployToken` function is fully public — no role restriction — and the only on-chain replay guard is the token-existence check `!isBridgeToken[nearToEthToken[metadata.token]]`, which only prevents replay **on the same chain**:

```solidity
require(
    !isBridgeToken[nearToEthToken[metadata.token]],
    "ERR_TOKEN_EXIST"
);
``` [3](#0-2) 

Because `nearBridgeDerivedAddress` is the same NEAR-derived key across every EVM deployment, a signature that is valid on chain A is cryptographically valid on chain B, C, etc. There is no mechanism preventing cross-chain reuse.

The `OmniBridgeWormhole` contract inherits `deployToken` unchanged, so the vulnerability applies to both variants. [4](#0-3) 

---

### Impact Explanation

An attacker can deploy an unauthorized bridge token for any NEAR token on any EVM chain where the OmniBridge is live, without NEAR's per-chain authorization. This matches the allowed High impact: **"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, token deployment, or message execution."**

Concrete harm: users who receive tokens via `finTransfer` on the unauthorized chain (e.g., because the attacker also controls a relayer on that chain, or because the NEAR bridge later registers the chain's factory) hold tokens that cannot be bridged back if the NEAR bridge does not recognize the chain's factory — resulting in permanent fund lock. Alternatively, if the NEAR bridge does register the factory, the attacker has bootstrapped an unauthorized bridge leg that can be used to drain collateral.

---

### Likelihood Explanation

- `deployToken` is a public, permissionless function — any EOA can call it.
- The `signatureData` and `metadata` arguments are fully visible in the calldata of the original on-chain transaction on chain A.
- The OmniBridge is deployed on multiple EVM chains sharing the same `nearBridgeDerivedAddress`.
- No off-chain coordination or privileged access is required; the attacker only needs to copy calldata from one chain to another.

---

### Recommendation

Include `omniBridgeChainId` in the signed payload for `deployToken`, consistent with how `finTransfer` already binds signatures to a specific chain:

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

This ensures a signature issued for chain A cannot be accepted on chain B.

---

### Proof of Concept

1. NEAR bridge MPC signs a `DeployToken` payload for token `"usdc.near"` (name `"USD Coin"`, symbol `"USDC"`, decimals `6`) targeting Ethereum.
2. A legitimate relayer submits `deployToken(sig, metadata)` on Ethereum; the transaction is mined and `sig` is publicly visible in calldata.
3. Attacker copies `sig` and `metadata` verbatim.
4. Attacker calls `deployToken(sig, metadata)` on BNB Chain, where `OmniBridgeWormhole` is deployed with the same `nearBridgeDerivedAddress`.
5. `ECDSA.recover(keccak256(borshEncoded), sig)` returns `nearBridgeDerivedAddress` — the check passes — because the payload is chain-agnostic.
6. `nearToEthToken["usdc.near"]` is zero on BNB Chain, so `!isBridgeToken[address(0)]` is `true` — the existence guard passes.
7. A new `BridgeToken` proxy for `"usdc.near"` is deployed on BNB Chain without any authorization from the NEAR bridge for that chain.
8. Steps 3–7 can be repeated for every other EVM chain where the OmniBridge is deployed. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-195)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
        if (tokenImplementationAddress == address(0)) {
            revert TokenImplementationNotSet();
        }
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

        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
        uint8 decimals = _normalizeDecimals(metadata.decimals);

        // slither-disable-next-line reentrancy-no-eth
        address bridgeTokenProxy = address(
            new ERC1967Proxy(
                tokenImplementationAddress,
                abi.encodeWithSelector(
                    BridgeToken.initialize.selector,
                    metadata.name,
                    metadata.symbol,
                    decimals
                )
            )
        );

        deployTokenExtension(
            metadata.token,
            bridgeTokenProxy,
            decimals,
            metadata.decimals
        );

        emit BridgeTypes.DeployToken(
            bridgeTokenProxy,
            metadata.token,
            metadata.name,
            metadata.symbol,
            decimals,
            metadata.decimals
        );

        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);

        return bridgeTokenProxy;
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L26-46)
```text
contract OmniBridgeWormhole is OmniBridge {
    IWormhole private _wormhole;
    // https://wormhole.com/docs/build/reference/consistency-levels
    uint8 private _consistencyLevel;
    uint32 public wormholeNonce;

    function initializeWormhole(
        address tokenImplementationAddress,
        address nearBridgeDerivedAddress,
        uint8 omniBridgeChainId,
        address wormholeAddress,
        uint8 consistencyLevel
    ) external initializer {
        initialize(
            tokenImplementationAddress,
            nearBridgeDerivedAddress,
            omniBridgeChainId
        );
        _wormhole = IWormhole(wormholeAddress);
        _consistencyLevel = consistencyLevel;
    }
```
