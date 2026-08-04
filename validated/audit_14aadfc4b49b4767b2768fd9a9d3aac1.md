### Title
Fee-on-Transfer/Rebasing Underlying Token Breaks WrappedHyperFungibleToken's Locked-Balance Invariant, Causing Fund Lock and Insolvency of the Shared Custody Pool - ([File: sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol])

### Summary
`WrappedHyperFungibleToken` (and its upgradeable variant) is a single custodial contract that locks an underlying ERC20 on `send()` and unlocks the same declared amount on `onAccept()` for every user of that deployment. Unlike `IntentGatewayV2`, which was hardened against fee-on-transfer/rebasing tokens by measuring actual balance deltas before crediting escrow, `WrappedHyperFungibleToken.send()` never verifies how much of the underlying token it actually received — it just trusts `params.amount` and dispatches that exact figure cross-chain. If `_underlying` is a fee-on-transfer or rebasing token, the contract's real balance grows more slowly than the sum of amounts it has promised to honor on `onAccept()`. This is the same accounting-mismatch bug class as the referenced RoyaltyVault/Splitter finding, applied to Hyperbridge's shared bridging custody pool.

### Finding Description
`send()` in `sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol` (lines 266–290):

```solidity
function send(HyperFungibleToken.SendParams calldata params) external payable whenNotPaused {
    uint256 msgValue = msg.value;
    if (_isWeth && msgValue >= params.amount) {
        msgValue = msgValue - params.amount;
        IWETH(_underlying).deposit{value: params.amount}();
    } else {
        IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
    }

    DispatchPost memory request = _buildDispatchPost(params);
    ...
}
``` [1](#0-0) 

`_buildDispatchPost` encodes `amount: params.amount` directly into the cross-chain `Message`, unconditionally:

```solidity
bytes memory body = abi.encode(HyperFungibleToken.Message({
    from: abi.encodePacked(msg.sender),
    to: params.to,
    amount: params.amount,
    data: params.data
}));
``` [2](#0-1) 

There is no balance-before/after check here, unlike the FoT-aware handling already present elsewhere in the codebase (`IntentGatewayV2.placeOrder`, which measures `IERC20(token).balanceOf(address(this))` before and after `safeTransferFrom` and mutates `order.inputs[i].amount` to the actual received amount):

```solidity
uint256 balBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
``` [3](#0-2) 

On the receiving side, `onAccept()` unconditionally transfers the *declared* `message.amount` out of the shared pool to the beneficiary:

```solidity
} else {
    IERC20(_underlying).safeTransfer(beneficiary, message.amount);
}
``` [4](#0-3) 

Because `WrappedHyperFungibleToken` is a shared custody pool across *all* users of the deployment (contrary to the doc's "no shared custody pool" claim, which only applies to the non-wrapped `HyperFungibleToken` that mints/burns rather than locks/unlocks), each `send()` with a FoT/rebasing underlying locks strictly less than `params.amount` while promising the full `params.amount` to be unlockable on the remote chain (and vice versa for the mirrored contract on that chain). Over repeated sends, the sum of amounts other users are entitled to withdraw via `onAccept()` exceeds the contract's actual token balance — exactly the RoyaltyVault/Splitter pattern where "the last user cannot withdraw."

### Impact Explanation
This directly causes fund loss/lock for legitimate bridge users: some unlock calls in `onAccept()` will revert (insufficient balance) once the deficit accumulates, permanently stranding funds for whichever users are unlucky enough to redeem last, while earlier users effectively received more value than what was truly custodied on their behalf. This is a false-accounting / fund-loss condition in a production bridge custody contract reachable through the completely standard, unprivileged `send()` entrypoint — no malicious relayer, prover, or governance actor is required, only a deployment configured (or later migrated) to wrap a FoT/rebasing ERC20.

### Likelihood Explanation
Likelihood depends on the underlying token chosen for a given `WrappedHyperFungibleToken` deployment. Since `configure()` accepts an arbitrary `underlying` address with no restriction against fee-on-transfer or rebasing semantics, any team deploying this wrapper for a token with transfer fees or negative rebasing (a common token category on EVM chains) would trigger this immediately on the very first `send()`. The codebase's own test suite already demonstrates awareness of, and remediation for, this exact issue in `IntentGatewayV2` (`FeeOnTransferToken` tests), but the identical fix was not applied to `WrappedHyperFungibleToken`/`WrappedHyperFungibleTokenUpgradeable`, indicating an inconsistent, incomplete mitigation across the codebase rather than a one-off oversight.

### Recommendation
Apply the same balance-delta pattern used in `IntentGatewayV2.placeOrder` to `WrappedHyperFungibleToken.send()`:
```solidity
uint256 balBefore = IERC20(_underlying).balanceOf(address(this));
IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
uint256 actualAmount = IERC20(_underlying).balanceOf(address(this)) - balBefore;
```
and dispatch `actualAmount` in the `Message` body instead of `params.amount`, so the amount promised cross-chain never exceeds what was actually locked. Alternatively, explicitly document and enforce (e.g. via a governance-set allow-list or a post-transfer balance assertion that reverts on mismatch) that only standard, non-fee, non-rebasing ERC20 tokens may be configured as `_underlying`.

### Proof of Concept
1. Deploy `WrappedHyperFungibleToken` with `_underlying` set to a 1% fee-on-transfer ERC20 (e.g. reuse the `FeeOnTransferToken` test helper already present in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol`).
2. User A calls `send({ amount: 1000e18, to: userA_remote, ... })`. `safeTransferFrom` pulls 1000e18 from A but the contract's actual balance only increases by 990e18 due to the transfer fee.
3. The dispatched `Message.amount` is still `1000e18` (uncorrected), so the peer `WrappedHyperFungibleToken` on the destination chain (or this same contract on a return trip) is entitled to unlock 1000e18.
4. Repeat step 2 for several users/rounds. Each round, actual custody grows by ~99% of the promised amount while total promised-and-payable obligations grow by 100%.
5. Eventually an `onAccept()` unlock call for `message.amount` reverts because `IERC20(_underlying).balanceOf(address(this))` is less than the requested transfer amount, permanently locking that user's funds while earlier withdrawers already extracted the surplus.

### Citations

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L238-243)
```text
        bytes memory body = abi.encode(HyperFungibleToken.Message({
            from: abi.encodePacked(msg.sender),
            to: params.to,
            amount: params.amount,
            data: params.data
        }));
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L266-281)
```text
    function send(HyperFungibleToken.SendParams calldata params) external payable whenNotPaused {
        uint256 msgValue = msg.value;
        if (_isWeth && msgValue >= params.amount) {
            msgValue = msgValue - params.amount;
            IWETH(_underlying).deposit{value: params.amount}();
        } else {
            IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
        }

        DispatchPost memory request = _buildDispatchPost(params);
        bytes32 commitment;
        if (msgValue > 0) {
            commitment = IDispatcher(_host).dispatch{value: msgValue}(request);
        } else {
            commitment = dispatchWithFeeToken(request);
        }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L322-324)
```text
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L289-291)
```text
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
```
