### Title
Missing Chain Identifier in `deployToken` Signed Hash Enables Cross-Chain Replay — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

The `deployToken` function in `OmniBridge.sol` constructs a signed hash that omits any chain identifier (`omniBridgeChainId`, `block.chainid`, or `address(this)`). A valid MPC-signed `deployToken` signature obtained on one EVM chain (e.g., Ethereum) can be replayed verbatim on any other EVM chain where the same NEAR token has not yet been deployed (e.g., Arbitrum), causing unauthorized token deployment and token-mapping registration on the unintended chain.

---

### Finding Description

In `OmniBridge.sol`, `deployToken` constructs its hash as:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
bytes32 hashed = keccak256(borshEncoded);
``` [1](#0-0) 

No chain identifier is included — neither `omniBridgeChainId` (a contract state variable), nor `block.chainid`, nor `address(this)`.

By contrast, `finTransfer` on the same contract explicitly embeds `omniBridgeChainId` twice in its hash (once for the token field, once for the recipient field):

```solidity
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),
Borsh.encodeAddress(payload.recipient),
``` [2](#0-1) 

This inconsistency means the MPC signer's `deployToken` signature is chain-agnostic: the same 65-byte signature passes `ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress` on every EVM chain where the bridge is deployed, because the hash is identical across all of them. [3](#0-2) 

The only on-chain replay guard is `require(!isBridgeToken[nearToEthToken[metadata.token]], "ERR_TOKEN_EXIST")`, which only prevents a second deployment of the same token **on the same chain** — it does not prevent replay on a different chain. [4](#0-3) 

After a successful replay, the attacker-triggered deployment:
1. Deploys a new `BridgeToken` proxy on the unintended chain.
2. Writes `isBridgeToken[proxy] = true`, `ethToNearToken[proxy] = metadata.token`, and `nearToEthToken[metadata.token] = proxy` on that chain.
3. Emits a `DeployToken` event and, in the Wormhole variant, publishes a Wormhole message via `deployTokenExtension` that notifies the NEAR bridge of the new token address on that chain. [5](#0-4) [6](#0-5) 

Once the NEAR bridge receives the Wormhole `DeployToken` notification for the unintended chain, it registers the attacker-deployed token address as the canonical address for that NEAR token on that chain. Subsequent `sign_transfer` calls on NEAR will resolve `get_token_address` to this address and the MPC signer will produce valid `finTransfer` signatures targeting the attacker-deployed token on the unintended chain. [7](#0-6) 

---

### Impact Explanation

**High — Proof/signature verification bypass enabling unauthorized token deployment and potential unauthorized minting.**

An attacker who replays a `deployToken` signature on an unintended chain:
- Registers an attacker-triggered token contract as the canonical bridge token for a NEAR asset on that chain.
- Causes the NEAR bridge to treat this deployment as legitimate (via the Wormhole `DeployToken` notification), permanently associating the NEAR token with the attacker-triggered EVM address on that chain.
- Blocks any future legitimate deployment of the same token on that chain (`ERR_TOKEN_EXIST` will revert all subsequent attempts).
- If the NEAR bridge subsequently signs `finTransfer` messages targeting this chain, tokens are minted on the attacker-deployed contract, which may have been initialized with a malicious or misconfigured implementation (e.g., if `tokenImplementationAddress` differs between chains), breaking bridge collateralization or misdirecting value.

---

### Likelihood Explanation

**Medium.** The Omni Bridge is explicitly deployed on multiple EVM chains (Ethereum, Arbitrum, etc.) with the same NEAR MPC key pair. Every `deployToken` call is a public transaction; its `signatureData` is visible on-chain the moment it is included in a block on chain A. An attacker simply monitors chain A for `DeployToken` events, extracts the `signatureData` from the transaction calldata, and submits the identical call on chain B before any legitimate deployment occurs there. No privileged access, leaked key, or colluding party is required.

---

### Recommendation

Include `omniBridgeChainId` in the `deployToken` borsh-encoded hash, mirroring the pattern already used in `finTransfer`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // domain separator: destination chain
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

Additionally, consider including `address(this)` to prevent replay between two bridge deployments that share the same `omniBridgeChainId` on the same EVM chain.

---

### Proof of Concept

1. The NEAR MPC signer produces a `deployToken` signature `sig` for NEAR token `"token.near"` on Ethereum (`omniBridgeChainId = 1`). The signed hash is `keccak256(abi.encodePacked(0x00, borsh("token.near"), borsh("Token"), borsh("TKN"), 0x12))`.
2. An attacker observes the Ethereum transaction and extracts `sig` from calldata.
3. The attacker calls `deployToken(sig, metadata)` on the Arbitrum deployment of `OmniBridge` (`omniBridgeChainId = 2`).
4. `ECDSA.recover(keccak256(borshEncoded), sig)` returns `nearBridgeDerivedAddress` — identical hash, identical signature, identical recovered address — so the check passes.
5. A `BridgeToken` proxy is deployed on Arbitrum; `nearToEthToken["token.near"]` is set to the new proxy address.
6. The Wormhole `DeployToken` message is published, notifying the NEAR bridge that `"token.near"` is now live on Arbitrum at the attacker-triggered address.
7. Any future legitimate attempt to deploy `"token.near"` on Arbitrum reverts with `ERR_TOKEN_EXIST`, permanently locking the mapping to the attacker-triggered deployment.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L151-153)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-299)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L48-70)
```text
    function deployTokenExtension(
        string memory token,
        address tokenAddress,
        uint8 decimals,
        uint8 originDecimals
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.DeployToken)),
            Borsh.encodeString(token),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            bytes1(decimals),
            bytes1(originDecimals)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

**File:** near/omni-bridge/src/lib.rs (L462-470)
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
