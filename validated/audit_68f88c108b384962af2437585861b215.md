### Title
CREATE-based Bridge Token Proxy Deployment Enables Reorg-Driven Token Address Collision and Fund Misdirection — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.deployToken()` deploys bridge token proxies using the `new ERC1967Proxy(...)` expression (the EVM `CREATE` opcode), whose output address depends solely on the OmniBridge contract's nonce. On chains susceptible to block reorganizations (Polygon, Arbitrum, etc.), an attacker can manipulate transaction ordering during a reorg to cause a different token's proxy to occupy the address originally assigned to a legitimate token. The NEAR side of the bridge records the EVM token address from the `DeployToken` event; if that record becomes stale after a reorg, the MPC will sign `finTransfer` calls pointing to the wrong (attacker-controlled) proxy, causing users to receive worthless tokens while their source-chain assets are permanently burned.

---

### Finding Description

In `OmniBridge.sol`, `deployToken()` deploys a new proxy using the `new` keyword:

```solidity
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
``` [1](#0-0) 

The deployed address is `keccak256(rlp(OmniBridge_address, nonce))`, where `nonce` increments with every `deployToken` call. There is no salt derived from the token identifier (`metadata.token`), so the address is entirely determined by call ordering.

After deployment, the address is recorded in both directions:

```solidity
isBridgeToken[address(bridgeTokenProxy)] = true;
ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
nearToEthToken[metadata.token] = address(bridgeTokenProxy);
``` [2](#0-1) 

The NEAR side of the bridge observes the `DeployToken` event and records the EVM address for each NEAR token. The MPC then uses this record when signing `finTransfer` payloads — specifically the `tokenAddress` field in `TransferMessagePayload`. [3](#0-2) 

In `finTransfer`, the signed `tokenAddress` is used directly to dispatch minting:

```solidity
} else if (isBridgeToken[payload.tokenAddress]) {
    IBridgeToken(payload.tokenAddress).mint(payload.recipient, payload.amount);
``` [4](#0-3) 

If a reorg causes a different token's proxy to occupy the address the NEAR side recorded for a legitimate token, every subsequent `finTransfer` for that legitimate token will mint the wrong (attacker-controlled) token to recipients.

---

### Impact Explanation

**Impact: High** — token-mapping corruption that breaks bridge collateralization and misdirects value.

A user who bridges `wrap.near` (a valuable token) from NEAR to EVM will have their NEAR tokens burned/locked on the NEAR side. The MPC signs a `finTransfer` with `tokenAddress = X` (the stale address). On the EVM side, `X` is now the proxy for `evil.near` (a worthless token). `finTransfer` calls `IBridgeToken(X).mint(recipient, amount)`, delivering worthless tokens. The user's funds are permanently lost — the NEAR-side burn is irreversible and the nonce is consumed on the EVM side, preventing any retry.

---

### Likelihood Explanation

**Likelihood: Low** — requires a block reorganization and an attacker who holds a valid MPC-signed `deployToken` payload for a different token.

- Reorgs occur regularly on Polygon (documented 120-block and 157-block reorgs) and are possible on Arbitrum/Optimism (fraud-proof reversions). The protocol targets "any EVM-compatible network."
- The attacker must possess a legitimately obtained MPC signature for `deployToken("evil.near")` — achievable by registering a real but worthless token on NEAR and requesting deployment through normal protocol flow.
- The attacker must control transaction ordering during the reorg window, which is feasible for a well-resourced actor on chains with short block times.

---

### Recommendation

Replace `new ERC1967Proxy(...)` with a `CREATE2` deployment using a salt derived from the token identifier:

```solidity
bytes32 salt = keccak256(abi.encodePacked(metadata.token));
address bridgeTokenProxy = address(
    new ERC1967Proxy{salt: salt}(
        tokenImplementationAddress,
        abi.encodeWithSelector(
            BridgeToken.initialize.selector,
            metadata.name,
            metadata.symbol,
            decimals
        )
    )
);
```

This makes the proxy address a deterministic function of the NEAR token ID, independent of call ordering or nonce. A reorg cannot change which address a given token maps to, eliminating the collision vector entirely.

---

### Proof of Concept

1. Attacker legitimately registers `evil.near` on NEAR and obtains a valid MPC-signed `deployToken("evil.near")` payload. They hold this transaction in reserve.

2. Relayer submits `deployToken("wrap.near")` to the EVM. It is included in block N with OmniBridge nonce = 0, deploying the `wrap.near` proxy at address **X**. The `DeployToken` event is emitted; the NEAR side records `"wrap.near" → X`.

3. A block reorganization occurs, reverting block N.

4. In the new block N, the attacker front-runs by submitting `deployToken("evil.near")` first (nonce = 0 → proxy at **X**). The relayer's `deployToken("wrap.near")` is included second (nonce = 1 → proxy at **Y**).

5. EVM state after reorg: `nearToEthToken["evil.near"] = X`, `nearToEthToken["wrap.near"] = Y`, `isBridgeToken[X] = true` (evil.near proxy).

6. NEAR side state (stale, from pre-reorg event): `"wrap.near" → X`.

7. A user bridges 1000 `wrap.near` tokens from NEAR to EVM. Their NEAR tokens are burned. The MPC signs a `finTransfer` payload with `tokenAddress = X`, `amount = 1000`.

8. `finTransfer` is called on EVM. `completedTransfers[nonce]` is false (the pre-reorg finalization was also reorganized away). The nonce is marked used.

9. `isBridgeToken[X]` is `true` → `IBridgeToken(X).mint(user, 1000)` is called. The user receives 1000 `evil.near` tokens (worthless).

10. The user's 1000 `wrap.near` tokens on NEAR are permanently burned. The destination nonce is consumed. No recourse exists. [5](#0-4)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L337-349)
```text
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
```

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L5-14)
```text
    struct TransferMessagePayload {
        uint64 destinationNonce;
        uint8 originChain;
        uint64 originNonce;
        address tokenAddress;
        uint128 amount;
        address recipient;
        string feeRecipient;
        bytes message;
    }
```
