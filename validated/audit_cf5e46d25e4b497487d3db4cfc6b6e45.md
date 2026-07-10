### Title
Missing Chain Binding in `deployToken` Signed Message Enables Cross-Chain Replay of Token Deployment Signatures — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

The `deployToken` function in `OmniBridge.sol` constructs a Borsh-encoded message for ECDSA signature verification that contains **no chain identifier** — neither the EVM `block.chainid` nor the contract's own `omniBridgeChainId`. Because all deployed EVM bridge instances share the same `nearBridgeDerivedAddress` (derived from the same NEAR MPC key), a valid `deployToken` signature obtained for one EVM chain (e.g., Ethereum mainnet) can be replayed verbatim on every other EVM chain the bridge is deployed on (Arbitrum, Base, BNB, Polygon, HyperEvm, Abs), deploying bridge tokens on chains for which NEAR never issued authorization.

### Finding Description

In `OmniBridge.sol`, `deployToken` builds its signed payload as:

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

The five fields encoded are: payload type, NEAR token ID, name, symbol, decimals. **No chain identifier is present.** The contract address, EVM `block.chainid`, and `omniBridgeChainId` are all absent.

Contrast this with `finTransfer`, which explicitly encodes `omniBridgeChainId` (twice) to bind the signature to the destination chain:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
    Borsh.encodeUint64(payload.destinationNonce),
    bytes1(payload.originChain),
    Borsh.encodeUint64(payload.originNonce),
    bytes1(omniBridgeChainId),          // destination chain binding
    Borsh.encodeAddress(payload.tokenAddress),
    Borsh.encodeUint128(payload.amount),
    bytes1(omniBridgeChainId),          // destination chain binding (recipient side)
    Borsh.encodeAddress(payload.recipient),
    ...
);
``` [2](#0-1) 

The `omniBridgeChainId` is a custom 1-byte Omni Bridge chain identifier (0 = Eth, 3 = Arb, 4 = Base, 5 = Bnb, 8 = Pol, 9 = HyperEvm, 11 = Abs) stored in contract storage: [3](#0-2) 

The `nearBridgeDerivedAddress` is the Ethereum address derived from the NEAR MPC key. Because the MPC key is shared across all EVM deployments, this address is identical on every EVM chain: [4](#0-3) 

The `ChainKind` enum confirms the set of EVM chains in scope: [5](#0-4) 

### Impact Explanation

An attacker who observes a valid `deployToken` transaction on Ethereum (signature + payload publicly visible on-chain) can immediately submit the identical `(signatureData, metadata)` pair to the `deployToken` function on Arbitrum, Base, BNB, Polygon, HyperEvm, and Abs. Each call will:

1. Pass `ECDSA.recover` because the hash is chain-agnostic and `nearBridgeDerivedAddress` is the same.
2. Deploy a new `BridgeToken` proxy on that chain, registering it in `isBridgeToken`, `ethToNearToken`, and `nearToEthToken`.

The deployed bridge token is a fully functional mint/burn token owned by the bridge. Once deployed, users can call `initTransfer` on the unauthorized chain, locking real ERC-20 value in the bridge. NEAR's indexer observes `InitTransfer` events from all EVM chains; if it processes these events (because the token ID matches a known NEAR token), it releases NEAR-side tokens against EVM-side collateral that was never properly authorized — breaking bridge collateralization. If NEAR does not process them, user funds are permanently locked on the unauthorized chain.

This matches: **High — Proof, signature, MPC verification bypass enabling unauthorized token deployment; and High — accounting corruption that breaks bridge collateralization or misdirects value.**

### Likelihood Explanation

Exploitation requires no special privilege. Any observer of a public Ethereum `deployToken` transaction can extract the calldata and replay it on other EVM chains in the same block. The NEAR MPC signs one metadata payload per token; the absence of chain binding means that single signature is valid everywhere. All target chains are live mainnet deployments sharing the same MPC-derived signer address.

### Recommendation

Include `omniBridgeChainId` (and ideally the contract address) in the `deployToken` Borsh-encoded message, mirroring the pattern already used in `finTransfer`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
    bytes1(omniBridgeChainId),          // ADD: chain binding
    Borsh.encodeAddress(address(this)), // ADD: contract address binding (optional but recommended)
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

The NEAR MPC must then sign chain-specific metadata payloads. The Starknet implementation's `fin_transfer` already passes `omni_bridge_chain_id` into `to_borsh()` as a model: [6](#0-5) 

### Proof of Concept

1. Monitor Ethereum mainnet for a `deployToken` call. Extract `signatureData` and `metadata` from calldata.
2. Compute the hash locally:
   ```
   keccak256(0x01 || borsh(metadata.token) || borsh(metadata.name) || borsh(metadata.symbol) || metadata.decimals)
   ```
   Confirm `ecrecover(hash, sig) == nearBridgeDerivedAddress`.
3. Submit the identical `(signatureData, metadata)` to `OmniBridge.deployToken` on Arbitrum (or Base, BNB, Polygon).
4. Observe the transaction succeeds: the token is deployed on Arbitrum, `isBridgeToken[proxy] = true`, `nearToEthToken[metadata.token] = proxy`.
5. Call `initTransfer` on Arbitrum for the newly deployed token, locking real tokens. Observe the `InitTransfer` event emitted with no NEAR-side authorization for this chain deployment. [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L41-43)
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

**File:** near/omni-types/src/lib.rs (L53-83)
```rust
pub enum ChainKind {
    #[default]
    #[serde(alias = "eth")]
    Eth,
    #[serde(alias = "near")]
    Near,
    #[serde(alias = "sol")]
    Sol,
    #[serde(alias = "arb")]
    Arb,
    #[serde(alias = "base")]
    Base,
    #[serde(alias = "bnb")]
    Bnb,
    #[serde(alias = "btc")]
    Btc,
    #[serde(alias = "zcash")]
    Zcash,
    #[serde(alias = "pol")]
    Pol,
    #[serde(rename = "HlEvm")]
    #[serde(alias = "hlevm")]
    #[strum(serialize = "HlEvm")]
    HyperEvm,
    #[serde(alias = "strk")]
    Strk,
    #[serde(alias = "abs")]
    Abs,
    #[serde(alias = "fogo")]
    Fogo,
}
```

**File:** starknet/src/omni_bridge.cairo (L252-254)
```text
            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
```
