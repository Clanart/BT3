### Title
Rebasing Token Rewards Permanently Locked in EVM Bridge - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary

`OmniBridge.initTransfer` locks native ERC20 tokens in the contract using the caller-supplied `amount`. For rebasing tokens (e.g., stETH, Aave aTokens), the bridge's actual token balance grows over time, but `finTransfer` only ever releases the original `amount` encoded in the MPC-signed payload. The accrued rebase rewards accumulate in the bridge contract with no recovery path, permanently locking user and protocol value.

### Finding Description

`OmniBridge.initTransfer` handles non-bridge, non-custom ERC20 tokens by pulling exactly `amount` from the caller into the bridge contract:

```solidity
// OmniBridge.sol line 407-411
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
```

The `amount` parameter is then emitted in `InitTransfer` and propagated cross-chain, where the NEAR side mints the equivalent bridged tokens. When the user bridges back, the NEAR side signs a `TransferMessagePayload` containing the original `amount`, and `finTransfer` on EVM releases exactly that amount:

```solidity
// OmniBridge.sol line 351-354
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
```

For rebasing tokens, `balanceOf(address(this))` for the bridge contract increases passively over time (the token contract internally adjusts all holders' balances upward). The bridge never reads its actual current balance — it only ever transfers the original `amount` that was signed by MPC. The delta between the actual balance and the sum of all deposited amounts accumulates in the contract indefinitely.

`OmniBridge.sol` contains no `rescue`, `sweep`, or emergency ERC20 withdrawal function. The only escape would be a contract upgrade by `DEFAULT_ADMIN_ROLE`, which is a privileged action outside the reach of any depositor.

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds in the bridge.**

Every rebasing token deposit causes a growing portion of the token balance to become permanently unclaimable. Over time, as more users deposit and rebase rewards accumulate, the locked surplus grows. Neither the original depositor nor any other party can recover the excess. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

**Medium.** The bridge accepts any ERC20 token that has been registered via `logMetadata` + `deployToken` on the NEAR side. Rebasing tokens such as stETH and Aave aTokens are among the most widely held ERC20 assets on Ethereum. Any user who bridges such a token triggers the vulnerability automatically — no special attacker action is required beyond a normal bridge deposit. The loss is proportional to the rebase rate and the time the tokens remain in the bridge.

### Recommendation

1. **Track total deposited amounts per token.** Maintain a `mapping(address => uint256) public totalDeposited` that is incremented on `initTransfer` and decremented on `finTransfer` for non-bridge tokens.
2. **Allow excess recovery.** Add an admin function that computes `IERC20(token).balanceOf(address(this)) - totalDeposited[token]` and transfers the surplus to a designated recipient (e.g., a DAO treasury).
3. **Alternatively, document and block rebasing tokens.** Add an explicit denylist or a check that reverts if the token is known to rebase, preventing the accounting mismatch from arising.

### Proof of Concept

1. Alice calls `initTransfer(stETH, 1000e18, ...)`. The bridge receives exactly `1000e18` stETH. The `InitTransfer` event records `amount = 1000e18`.
2. NEAR mints `1000e18` bridged-stETH to Alice's NEAR account.
3. Six months pass. stETH rebases; the bridge's `stETH.balanceOf(address(this))` is now `1050e18`.
4. Alice initiates a return transfer on NEAR. The NEAR bridge signs a payload with `amount = 1000e18` (the original deposited amount).
5. Alice calls `finTransfer` on EVM with the signed payload. The bridge transfers exactly `1000e18` stETH to Alice.
6. `50e18` stETH remains in the bridge contract. There is no function to withdraw it. It is permanently locked.

**Root cause lines:**

- Deposit locks caller-specified `amount`: [1](#0-0) 
- Withdrawal releases only the MPC-signed `payload.amount`, never the actual balance: [2](#0-1) 
- No rescue/sweep function exists anywhere in the contract: [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L407-411)
```text
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-598)
```text
    function pause(uint256 flags) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause(flags);
    }

    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }

    function upgradeToken(
        address tokenAddress,
        address implementation
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[tokenAddress], "ERR_NOT_BRIDGE_TOKEN");
        BridgeToken proxy = BridgeToken(tokenAddress);
        proxy.upgradeToAndCall(implementation, bytes(""));
    }

    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }

    receive() external payable {}

    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
    }

    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}

    uint256[49] private __gap;
```
