Looking at the external report's vulnerability class — an initialization parameter that defaults to a "bad" value, blocks withdrawal operations while allowing deposits, and cannot be changed without an upgrade — I need to find an analog in the Omni Bridge production files.

Let me trace the relevant code paths in `OmniBridge.sol`.

**`initTransfer` (deposit/lock path)** — does NOT use `omniBridgeChainId`: [1](#0-0) 

**`finTransfer` (withdrawal path)** — uses `omniBridgeChainId` TWICE in the borsh-encoded message for ECDSA signature verification: [2](#0-1) 

**`initialize`** — sets `omniBridgeChainId` once, with no setter function anywhere in the contract: [3](#0-2) 

Contrast with `nearBridgeDerivedAddress`, which has a dedicated setter: [4](#0-3) 

No equivalent `setOmniBridgeChainId` exists anywhere in the contract.

---

### Title
Incorrect `omniBridgeChainId` Initialization Permanently Freezes User Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
If `omniBridgeChainId` is set to an incorrect value (including `0`, the Solidity default for `uint8`) during `OmniBridge.initialize()`, all `finTransfer` calls will permanently fail signature verification while `initTransfer` continues to accept and lock/burn user tokens. Because no setter exists for `omniBridgeChainId`, the contract cannot be corrected without a full proxy upgrade, permanently freezing all in-flight user funds.

### Finding Description
`OmniBridge.initialize()` stores `omniBridgeChainId_` into `omniBridgeChainId` with no subsequent mutability:

```solidity
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {
    ...
    omniBridgeChainId = omniBridgeChainId_;
``` [3](#0-2) 

`finTransfer` embeds `omniBridgeChainId` at two positions in the borsh-encoded payload before hashing and recovering the signer:

```solidity
bytes1(omniBridgeChainId),          // destination chain for token address
Borsh.encodeAddress(payload.tokenAddress),
Borsh.encodeUint128(payload.amount),
bytes1(omniBridgeChainId),          // destination chain for recipient
Borsh.encodeAddress(payload.recipient),
...
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [2](#0-1) 

If `omniBridgeChainId` does not match the value the NEAR MPC/bridge used when signing, `ECDSA.recover` will return a different address and every `finTransfer` call reverts with `InvalidSignature`.

Meanwhile, `initTransfer` never reads `omniBridgeChainId`: [5](#0-4) 

Tokens are burned (bridge tokens) or transferred into the contract (native tokens) unconditionally. The deposit path is fully open; the withdrawal path is permanently closed.

Unlike `nearBridgeDerivedAddress` — which has `setNearBridgeDerivedAddress` for post-deployment correction — `omniBridgeChainId` has no setter: [4](#0-3) 

The only remediation path is a full UUPS proxy upgrade, which is a protocol-level intervention.

### Impact Explanation
**Critical — Permanent freezing of user and protocol funds.**

- Any user who calls `initTransfer` after a bad initialization has their ERC-20 tokens burned or locked in the contract with no recovery path.
- Any cross-chain transfer originating from NEAR targeting this EVM deployment will have its NEAR-side tokens locked in the NEAR bridge while `finTransfer` on EVM permanently reverts.
- The `completedTransfers` nonce bitmap is never set for failed calls, so retrying with the same nonce is possible but will always fail for the same reason.
- No user-callable escape hatch exists.

### Likelihood Explanation
**Low-Medium.** The scenario requires an admin initialization error — specifically passing `0` (the Solidity default for `uint8`) or a wrong chain ID. This is directly analogous to the external report's `allowControlled = false` default. The risk is elevated because:
- `uint8` defaults to `0` in Solidity, and a deployment script that omits or misconfigures this parameter silently produces a broken contract.
- The error is not detectable until the first `finTransfer` attempt, by which time user funds may already be locked.
- `nearBridgeDerivedAddress` has a setter (indicating the developers anticipated the need for post-deployment correction) but `omniBridgeChainId` does not, creating an asymmetric and surprising immutability.

### Recommendation
1. Add a privileged setter for `omniBridgeChainId`, consistent with the existing `setNearBridgeDerivedAddress` pattern.
2. Add a non-zero validation in `initialize`: `require(omniBridgeChainId_ != 0, "ERR_INVALID_CHAIN_ID")`.
3. Consider emitting an event on initialization so off-chain monitoring can detect misconfiguration before user funds are at risk.

### Proof of Concept
1. Admin deploys `OmniBridge` proxy and calls `initialize(impl, nearDerivedAddr, 0)` — accidentally passing `0` for `omniBridgeChainId` (the Solidity default).
2. User calls `initTransfer(USDC, 1000e6, 0, 0, "alice.near", "")` — 1000 USDC is transferred into the bridge contract. Transaction succeeds.
3. NEAR bridge MPC signs a `finTransfer` payload encoding the correct EVM chain ID (e.g., `1` for Ethereum mainnet) at both chain-ID positions.
4. Relayer calls `finTransfer(sig, payload)` on EVM.
5. `OmniBridge` reconstructs the borsh encoding with `omniBridgeChainId = 0` at both positions — producing a different hash than what was signed.
6. `ECDSA.recover(hashed, sig)` returns an address ≠ `nearBridgeDerivedAddress`.
7. Transaction reverts: `InvalidSignature()`.
8. User's 1000 USDC is permanently locked in the bridge. No user-callable function can release it. A proxy upgrade is the only remediation.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L289-313)
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

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L568-572)
```text
    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }
```
