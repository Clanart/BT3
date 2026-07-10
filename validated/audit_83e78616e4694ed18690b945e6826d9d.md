### Title
MetadataPayload Signature Replay After `removeCustomToken` Enables Duplicate Proxy Deployment — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`deployToken` has no replay protection on its MPC signature. After an admin legitimately calls `removeCustomToken`, the existence guard evaluates to `true` again for the same NEAR token ID, allowing any unprivileged caller to replay the original valid signature and deploy a second `ERC1967Proxy` for the same NEAR token.

---

### Finding Description

**`MetadataPayload` carries no replay-prevention field.**

`BridgeTypes.MetadataPayload` contains only `token`, `name`, `symbol`, and `decimals` — no nonce, timestamp, expiry, or one-time-use marker. [1](#0-0) 

The hash signed by the MPC is therefore identical every time the same token metadata is submitted. [2](#0-1) 

**The only existence guard is `!isBridgeToken[nearToEthToken[metadata.token]]`.**

After a successful first deployment, `nearToEthToken[token] = proxy1` and `isBridgeToken[proxy1] = true`, so the guard blocks re-entry. [3](#0-2) 

**`removeCustomToken` resets both mappings to zero/false.**

```
delete isBridgeToken[tokenAddress];          // isBridgeToken[proxy1] = false
delete nearToEthToken[ethToNearToken[tokenAddress]]; // nearToEthToken[token] = address(0)
delete ethToNearToken[tokenAddress];
``` [4](#0-3) 

After removal:
- `nearToEthToken[token]` → `address(0)`
- `isBridgeToken[address(0)]` → `false` (never set)
- Guard: `!isBridgeToken[address(0)]` → `!false` → **`true`** — the check passes again.

There is no `usedSignatures` mapping or equivalent in `deployToken`; `completedTransfers` only covers `finTransfer`. [5](#0-4) 

---

### Impact Explanation

An unprivileged relayer replays the original MPC-signed `MetadataPayload` and a second `ERC1967Proxy` (`proxy2`) is deployed at a new address for the same NEAR token ID. After the replay:

- `nearToEthToken[token]` → `proxy2`
- `isBridgeToken[proxy2]` → `true`
- `proxy1` still exists with all prior balances, but `isBridgeToken[proxy1]` = `false`

Consequences:
1. **Broken canonical mapping** — the NEAR side and EVM side disagree on which proxy is authoritative.
2. **Unbacked minting** — `finTransfer` calls targeting `proxy2` will `mint` new tokens against the new proxy, while `proxy1` holders retain their balances. Total EVM supply exceeds NEAR-locked supply.
3. **Locked balances on `proxy1`** — `initTransfer` from `proxy1` falls through to the `safeTransferFrom` path (not `burn`), breaking outbound transfers for existing holders. [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

`removeCustomToken` is a legitimate admin operation (e.g., replacing a broken or compromised custom token). The admin is not acting maliciously; the vulnerability is that the post-removal state re-opens the signature replay window. Any observer who saved the original `deployToken` calldata from the mempool or chain history can immediately replay it. No special access is required for the exploit step itself.

---

### Recommendation

1. **Track used metadata signatures**: add a `mapping(bytes32 => bool) public usedMetadataSignatures` and revert if `usedMetadataSignatures[hashed]` is already set.
2. **Add a nonce or expiry to `MetadataPayload`**: include a monotonic nonce or block-timestamp expiry in the signed payload so each MPC signature is single-use.
3. **Prevent `removeCustomToken` from clearing `deployToken`-deployed proxies**, or require re-registration to go through a separate privileged path that does not accept old signatures.

---

### Proof of Concept

```solidity
// 1. Deploy token with valid MPC signature
address proxy1 = bridge.deployToken(sig, metadata); // nearToEthToken[token] = proxy1

// 2. Admin removes the token (legitimate operation)
bridge.removeCustomToken(proxy1);
// nearToEthToken[token] = address(0), isBridgeToken[proxy1] = false

// 3. Unprivileged relayer replays the SAME sig and metadata
address proxy2 = bridge.deployToken(sig, metadata); // guard: !isBridgeToken[address(0)] == true → passes

assert(proxy2 != proxy1);                          // new proxy deployed
assert(bridge.nearToEthToken(token) == proxy2);    // canonical mapping now points to proxy2
// proxy1 balances are stranded; proxy2 can receive new mints
```

### Citations

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L16-21)
```text
    struct MetadataPayload {
        string token;
        string name;
        string symbol;
        uint8 decimals;
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L44-44)
```text
    mapping(uint64 => bool) public completedTransfers;
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L404-406)
```text
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
```
