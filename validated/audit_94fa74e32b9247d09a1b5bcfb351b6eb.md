### Title
Missing Chain ID Domain Separation in `deployToken` Signed Message Enables Cross-Chain Replay — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

The `deployToken` function in `OmniBridge.sol` constructs a Borsh-encoded message for ECDSA signature verification that omits the `omniBridgeChainId`. In contrast, `finTransfer` explicitly binds the signed message to the destination chain by including `omniBridgeChainId` twice. Because the same `nearBridgeDerivedAddress` (derived from the NEAR MPC key) is used across all EVM deployments, a valid `deployToken` signature obtained from one EVM chain can be replayed verbatim on any other EVM chain where the same NEAR token has not yet been deployed, creating an unauthorized token mapping.

### Finding Description

In `finTransfer`, the Borsh-encoded payload includes `omniBridgeChainId` at two positions (destination chain for the token address and for the recipient), providing chain-domain separation: [1](#0-0) 

In `deployToken`, the signed payload is:

```
PayloadType.Metadata | token | name | symbol | decimals
```

No `omniBridgeChainId` is included: [2](#0-1) 

The only replay guard in `deployToken` is the per-chain idempotency check `!isBridgeToken[nearToEthToken[metadata.token]]`, which only prevents re-deployment on the *same* chain: [3](#0-2) 

Because the signed message contains no chain binding, the identical `(signatureData, metadata)` tuple that passes on Ethereum will also pass on Arbitrum, Base, BNB, or any other EVM deployment sharing the same `nearBridgeDerivedAddress`.

The `nearBridgeDerivedAddress` is a single MPC-derived Ethereum address set at initialization and shared across all EVM deployments: [4](#0-3) 

### Impact Explanation

An attacker who observes a legitimate `deployToken` call on chain A (e.g., Ethereum) can immediately replay the same calldata on chain B (e.g., Arbitrum) before the protocol's own relayer does so. This:

1. **Sets `nearToEthToken[metadata.token]` on chain B** to an attacker-triggered deployment, establishing an unauthorized token mapping.
2. **Permanently blocks the legitimate deployment on chain B** — any subsequent call fails with `ERR_TOKEN_EXIST`, because the idempotency check uses the already-set mapping.
3. **Allows users to bridge NEAR-native tokens to chain B** via the unauthorized token. The NEAR bridge's `sign_transfer` will sign transfers to chain B if it is a registered destination, and `finTransfer` on chain B will mint against the unauthorized token (which is `isBridgeToken = true`).
4. If the protocol intended to deploy on chain B with a different `tokenImplementationAddress` (e.g., an upgraded implementation), the unauthorized deployment locks in the old implementation, corrupting the intended token-mapping configuration.

This constitutes token-mapping corruption that misdirects value and can permanently freeze the legitimate deployment path for that token on the target chain.

### Likelihood Explanation

- The same `nearBridgeDerivedAddress` is used across all EVM chains (it is derived from the NEAR MPC key, which is chain-agnostic).
- `deployToken` calldata is fully public on-chain; any observer can extract `(signatureData, metadata)` from a confirmed transaction on chain A.
- The attacker needs no special privileges — `deployToken` is a permissionless external function.
- The window is any time before the protocol's own relayer deploys on chain B, which can be hours or days after chain A deployment.

### Recommendation

Include `omniBridgeChainId` in the Borsh-encoded message that is signed for `deployToken`, mirroring the pattern already used in `finTransfer`:

```solidity
bytes memory borshEncoded = bytes.concat(
    bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
+   bytes1(omniBridgeChainId),          // chain domain separator
    Borsh.encodeString(metadata.token),
    Borsh.encodeString(metadata.name),
    Borsh.encodeString(metadata.symbol),
    bytes1(metadata.decimals)
);
```

The NEAR MPC signing logic must be updated correspondingly to include the target chain ID when producing `deployToken` signatures.

### Proof of Concept

1. Protocol deploys `OmniBridgeWormhole` on Ethereum (chainId=1, `omniBridgeChainId=2`) and Arbitrum (`omniBridgeChainId=3`), both with the same `nearBridgeDerivedAddress = 0xABCD...`.
2. NEAR MPC signs a `deployToken` message for `wrap.near` (name="Wrapped NEAR", symbol="wNEAR", decimals=24). The signed hash is `keccak256(0x01 | borsh("wrap.near") | borsh("Wrapped NEAR") | borsh("wNEAR") | 0x18)` — no chain ID.
3. Relayer submits the transaction on Ethereum. Attacker observes the confirmed tx and extracts `(signatureData, metadata)`.
4. Attacker calls `OmniBridgeWormhole.deployToken(signatureData, metadata)` on Arbitrum. The `ECDSA.recover` check passes (same hash, same signer), the idempotency check passes (token not yet deployed on Arbitrum), and a new `BridgeToken` proxy is deployed.
5. `nearToEthToken["wrap.near"]` on Arbitrum is now set to the attacker-triggered proxy. `isBridgeToken[proxy] = true`.
6. When the protocol's relayer later tries to deploy `wrap.near` on Arbitrum, the call reverts with `ERR_TOKEN_EXIST`.
7. Users bridging `wrap.near` from NEAR to Arbitrum receive tokens from the unauthorized deployment; the legitimate deployment path is permanently blocked. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L72-86)
```text
    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
        tokenImplementationAddress = tokenImplementationAddress_;
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
        omniBridgeChainId = omniBridgeChainId_;

        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init_unchained();
        _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-313)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

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

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```
