### Title
`deployToken` MPC Signature Replay After `removeCustomToken` Enables Unauthorized Token Redeployment — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`deployToken` has no used-signature or nonce tracking. Its only guard against re-deployment is `!isBridgeToken[nearToEthToken[metadata.token]]`. After an admin legitimately calls `removeCustomToken`, that guard evaluates to `true` again, allowing any unprivileged relayer to replay the original MPC deploy signature and register a fresh, zero-supply proxy as the canonical token for the NEAR token ID.

---

### Finding Description

`finTransfer` correctly prevents replay via `completedTransfers[payload.destinationNonce]`: [1](#0-0) 

`deployToken` has no equivalent. Its only re-deployment guard is: [2](#0-1) 

This evaluates `isBridgeToken[nearToEthToken[metadata.token]]`. After `removeCustomToken` runs: [3](#0-2) 

both `nearToEthToken[metadata.token]` and `isBridgeToken[address(0)]` are `false`/zero, so the guard becomes `!false = true` and passes. The MPC signature itself covers only `(PayloadType.Metadata, token, name, symbol, decimals)` — no nonce, no chain ID, no timestamp — so it is permanently replayable: [4](#0-3) 

---

### Impact Explanation

An unprivileged relayer replays the original `deployToken` call. A new `ERC1967Proxy` is deployed and written into `nearToEthToken[metadata.token]` and `isBridgeToken`: [5](#0-4) 

The new proxy has zero supply. All subsequent `finTransfer` calls for that NEAR token ID will mint on the new proxy. Holders of the original proxy tokens are left with tokens that are no longer the canonical bridge token, while the new proxy can receive unbacked mints — a direct supply/collateralization corruption.

---

### Likelihood Explanation

`removeCustomToken` is a routine admin operation (token migration, custom-minter replacement, misconfiguration fix). Any relayer who observed the original `deployToken` transaction on-chain has the full `signatureData` and `metadata` needed to replay it. No privileged access, leaked key, or colluding MPC signer is required after the admin action.

---

### Recommendation

Track used deploy signatures the same way transfer nonces are tracked. Add a `mapping(bytes32 => bool) public usedDeploySignatures` and mark the signature hash as used inside `deployToken` before deploying the proxy:

```solidity
bytes32 sigHash = keccak256(signatureData);
require(!usedDeploySignatures[sigHash], "ERR_SIGNATURE_ALREADY_USED");
usedDeploySignatures[sigHash] = true;
```

Alternatively, include a monotonic nonce in the `MetadataPayload` that the MPC signs, and track used nonces identically to `completedTransfers`.

---

### Proof of Concept

```
1. Admin deploys token: deployToken(sig, {token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})
   → nearToEthToken["foo.near"] = proxyA, isBridgeToken[proxyA] = true

2. Admin calls removeCustomToken(proxyA)
   → nearToEthToken["foo.near"] = address(0), isBridgeToken[proxyA] = false

3. Relayer replays: deployToken(sig, {token:"foo.near", name:"Foo", symbol:"FOO", decimals:18})
   Guard: !isBridgeToken[nearToEthToken["foo.near"]] = !isBridgeToken[address(0)] = true → passes
   Signature: valid (same MPC sig, no nonce)
   → nearToEthToken["foo.near"] = proxyB (new zero-supply proxy)

4. assert(nearToEthToken["foo.near"] != proxyA)  // hijacked
   assert(isBridgeToken[proxyB] == true)          // new canonical token
   // All future finTransfer mints go to proxyB; proxyA holders are stranded
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L120-127)
```text
    function removeCustomToken(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        delete isBridgeToken[tokenAddress];
        delete nearToEthToken[ethToNearToken[tokenAddress]];
        delete ethToNearToken[tokenAddress];
        delete customMinters[tokenAddress];
    }
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L190-192)
```text
        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```
