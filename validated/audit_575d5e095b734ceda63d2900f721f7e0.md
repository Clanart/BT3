### Title
Missing Chain ID in `deployToken` Signature Hash Enables Cross-Chain Metadata Signature Replay — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `deployToken` function in `OmniBridge.sol` computes a signature hash that does **not** include the destination chain ID (`omniBridgeChainId`). Because the same NEAR MPC-derived address (`nearBridgeDerivedAddress`) is used across all EVM deployments, a valid metadata signature produced for one EVM chain (e.g., Ethereum) can be replayed verbatim on any other EVM chain (e.g., Arbitrum, Base, BNB) to deploy the same bridge token without NEAR's explicit authorization for that chain.

---

### Finding Description

`deployToken` constructs its Borsh-encoded hash as:

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

No chain identifier is included in this hash. By contrast, `finTransfer` explicitly encodes `omniBridgeChainId` twice in its hash (once for the token address field and once for the recipient field), binding each transfer signature to a specific chain:

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

The `nearBridgeDerivedAddress` is a single Ethereum-format address derived from the NEAR MPC key and is shared across all EVM chain deployments. A signature that satisfies `ECDSA.recover(hashed, signatureData) == nearBridgeDerivedAddress` on Ethereum will satisfy the same check on Arbitrum, Base, BNB, Polygon, HyperEVM, and Abstract — because the hash is identical on all of them. [3](#0-2) 

The only guard against re-use of the same signature is:

```solidity
require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST");
``` [4](#0-3) 

This check is per-chain-contract state, so it only prevents replay **on the same chain**. It does nothing to prevent the same signature from being submitted to a different chain's `OmniBridge` contract where the token has not yet been deployed.

---

### Impact Explanation

An attacker who observes a successful `deployToken` call on chain A can immediately submit the same `(signatureData, metadata)` tuple to the `OmniBridge` contract on chain B. The result:

1. **Unauthorized token deployment**: A bridge token for `metadata.token` is deployed on chain B without NEAR's MPC ever signing a message authorizing deployment on chain B.
2. **Permanent blocking of legitimate deployment**: Because there is no `removeBridgeToken` function (only `removeCustomToken`, which only removes admin-added custom tokens), once the attacker's replayed deployment succeeds, NEAR can never legitimately deploy the same token on chain B — every future attempt will revert with `ERR_TOKEN_EXIST`.
3. **Disruption of bridging**: NEAR's bridge contract on NEAR tracks which EVM address corresponds to each token on each chain. If the attacker's deployment address differs from what NEAR would have computed (e.g., due to different deployer nonce or salt), NEAR's `finTransfer` signatures for chain B will reference a different address, making the attacker-deployed token permanently unmintable and blocking all bridging of that token to chain B.

This matches the allowed High impact: **"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized token deployment"** and **"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

---

### Likelihood Explanation

- The attack requires no special privileges — `deployToken` is a public, permissionless function callable by any address.
- The attacker only needs to monitor the mempool or finalized blocks on any one EVM chain for a `deployToken` transaction, then replay the calldata on other chains.
- All Omni Bridge EVM deployments share the same `nearBridgeDerivedAddress`, making every chain a valid replay target.
- The attack is cheap (one transaction per target chain) and irreversible (no removal path for bridge tokens).

---

### Recommendation

Include `omniBridgeChainId` in the Borsh-encoded payload hashed for `deployToken`, mirroring the pattern already used in `finTransfer`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

This ensures that a metadata signature produced for Ethereum cannot be accepted by the Arbitrum or any other chain's `OmniBridge` contract. The NEAR MPC signing service must correspondingly include the destination chain ID when producing metadata signatures.

---

### Proof of Concept

1. NEAR's MPC signs a `MetadataPayload` for token `"usdc.near"` targeting Ethereum (`omniBridgeChainId = 0`). The signed hash covers only `[PayloadType.Metadata, "usdc.near", "USD Coin", "USDC", 6]`.
2. A relayer calls `OmniBridge.deployToken(sig, payload)` on Ethereum — succeeds, token deployed at address `T_eth`.
3. Attacker copies `(sig, payload)` and calls `OmniBridge.deployToken(sig, payload)` on Arbitrum (`omniBridgeChainId = 2`). The hash is identical; `ECDSA.recover` returns `nearBridgeDerivedAddress`; the check passes; token deployed at address `T_arb`.
4. NEAR's bridge has no record of `T_arb` as the USDC token on Arbitrum. NEAR's MPC will sign `finTransfer` messages for Arbitrum referencing a different address (the one NEAR would have computed). `T_arb` can never receive minted tokens.
5. When NEAR later tries to legitimately deploy USDC on Arbitrum, the call reverts with `ERR_TOKEN_EXIST`. USDC bridging to Arbitrum is permanently blocked. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L41-42)
```text
    address public nearBridgeDerivedAddress;
    uint8 public omniBridgeChainId;
```

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
