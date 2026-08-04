## Analysis

The 1inch report's core invariant is: **a contract must not promise/record more tokens than it actually holds**, because unchecked deficits eventually make a legitimate transfer fail (or, worse, let one claimant drain funds owed to others). The strongest local analog in Hyperbridge is the lock-side of the cross-chain fungible-token bridge, `WrappedHyperFungibleToken.send()` (and its `...Upgradeable` twin), which locks an arbitrary "existing ERC20" as home-chain collateral without verifying the amount actually received.

### Title
Unverified ERC20 receipt amount in `WrappedHyperFungibleToken.send()` breaks 1:1 lock/unlock custody for fee-on-transfer or balance-manipulating underlying tokens - (File: `sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol`)

### Summary
`send()` locks `params.amount` of `_underlying` via `safeTransferFrom` and then dispatches a cross-chain message that unconditionally carries `params.amount` as the bridged value, without checking how many tokens the contract actually received. [1](#0-0) 
Unlike `IntentGatewayV2.sol`/`IntentsBase.sol`, which explicitly snapshot balances before/after a transfer and use the *actual delta* for escrow accounting and cross-chain commitments to defend against fee-on-transfer/deflationary tokens, [2](#0-1) 
`WrappedHyperFungibleToken` never performs that check.

### Finding Description
In `send()`, the ERC20 branch does:
```solidity
IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
```
and immediately uses `params.amount` as the amount encoded into the outbound `Message`, which the remote/peer contract will honor at face value on `onAccept`/mint or on a later unlock. [3](#0-2) 
`safeTransferFrom` only guarantees the call succeeds, not that `address(this)`'s balance increased by exactly `amount` — deflationary/fee-on-transfer tokens, rebasing tokens, or tokens with hookable transfer logic can deliver strictly less. There is no `balanceOf` check before/after the transfer, and no adjustment of the amount encoded into the dispatched message, mirroring exactly the class of bug described in the CumulativeMerkleDrop report ("balance can be less than amount") but here applied to bridge collateral instead of a merkle-drop payout.

On `onAccept` (the unlock/receive side used for return trips and native-underlying withdrawals), the contract pays out the full `message.amount` from its own balance: [4](#0-3) 
Because the lock side never reconciled how much collateral was actually escrowed, the aggregate amount the contract is obligated to release across all in-flight and returning transfers can exceed what it actually holds. `onPostRequestTimeout` has the identical unchecked-amount pattern for refunds.

The same pattern is duplicated verbatim in the upgradeable variant. [5](#0-4) 

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" custody invariant. Any unprivileged user who bridges a fee-on-transfer/deflationary ERC20 through this contract silently under-collateralizes the pool: the peer chain mints/credits `params.amount`, but the home-chain contract only ever received `amount − fee`. As more such transfers occur, the contract's real token balance falls behind its outstanding obligations. The first consequence is a denial-of-service — a legitimate unlock/refund reverts on `safeTransfer` due to insufficient balance, permanently locking other users' funds. Depending on transfer order, this can also let a later claimant drain the remaining collateral, leaving an earlier depositor's unlock unpayable (fund loss for that depositor). This is exactly the "loss of funds" / broken custody category the bounty targets, and requires no malicious peer, relayer, or admin — only an ordinary user choosing (or being tricked into using) a fee-on-transfer token as `_underlying`, or the deploying team configuring one without realizing the risk (the docs describe "existing ERC20 tokens" generically, with no restriction against fee-on-transfer tokens).

### Likelihood Explanation
Likelihood is moderate-to-high in practice: fee-on-transfer tokens are common in production (many "reflection"/tax tokens), and nothing in `configure()` or `send()` validates or restricts `_underlying`'s transfer semantics. [6](#0-5) 
The protocol clearly recognizes this exact class of risk elsewhere (IntentGatewayV2 explicitly guards for it), which shows the omission here is a genuine gap rather than an accepted design tradeoff.

### Recommendation
Adopt the same actual-balance-delta pattern used in `IntentGatewayV2`/`IntentsBase`: snapshot `IERC20(_underlying).balanceOf(address(this))` before and after `safeTransferFrom`, use the delta as the amount encoded in the dispatched `Message`, and/or add the explicit invariant check recommended by the original report:
```solidity
uint256 balBefore = IERC20(_underlying).balanceOf(address(this));
IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
uint256 received = IERC20(_underlying).balanceOf(address(this)) - balBefore;
// use `received` (not params.amount) when building the DispatchPost body
```
Alternatively, explicitly document/enforce (e.g., via a registry allow-list) that only standard, non-fee-on-transfer, non-rebasing ERC20s may be configured as `_underlying`.

### Proof of Concept
1. Deploy `WrappedHyperFungibleToken`, `configure()` it with `_underlying` set to a deflationary ERC20 that takes, e.g., a 2% fee on every `transfer`/`transferFrom`.
2. User A calls `send({amount: 100e18, ...})`. `safeTransferFrom` moves only `98e18` into the contract (`balanceOf(address(this))` increases by `98e18`), but the dispatched `Message.amount` is `100e18`.
3. The peer chain mints/credits `100e18` to the recipient based on the message.
4. Repeat with User B, C, ... — each iteration the contract's real backing lags 2% behind what it has promised remote chains.
5. Once cumulative shortfall exceeds the contract's remaining `_underlying` balance, any subsequent legitimate unlock (`onAccept` releasing `message.amount` back to a beneficiary, or `onPostRequestTimeout` refunding a sender) reverts in `safeTransfer` due to insufficient balance — permanently locking that user's collateral while earlier claimants may have already drained the remainder.

### Citations

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L48-64)
```text
contract WrappedHyperFungibleToken is ERC165, HyperApp, Ownable, Pausable {
    using SafeERC20 for IERC20;

    /**
     * @title WrappedConfigOptions
     * @notice Configuration parameters for WrappedHyperFungibleToken
     */
    struct WrappedConfigOptions {
        /// @notice Address of the ISMP host contract on this chain
        address host;
        /// @notice Address of the CallDispatcher contract for executing calldata on receive
        address dispatcher;
        /// @notice Address of the underlying ERC20 token to wrap
        address underlying;
        /// @notice Whether the underlying token is WETH (enables native ETH refunds on timeout)
        bool isWeth;
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L203-253)
```text
        delete _supportedChains[chainId];
    }

    /**
     * @notice Pauses all cross-chain operations (send and receive)
     * @dev Only callable by the contract owner
     */
    function pause() external onlyOwner {
        _pause();
    }

    /**
     * @notice Unpauses all cross-chain operations
     * @dev Only callable by the contract owner
     */
    function unpause() external onlyOwner {
        _unpause();
    }

    /**
     * @notice Returns the fee in native currency for sending a cross-chain transfer.
     * @param params The send parameters
     * @return The fee amount in native currency
     */
    function quote(HyperFungibleToken.SendParams calldata params) public returns (uint256) {
        return quote(_buildDispatchPost(params));
    }

    /**
     * @dev Builds the DispatchPost from SendParams.
     */
    function _buildDispatchPost(HyperFungibleToken.SendParams calldata params) internal view returns (DispatchPost memory) {
        bytes memory dest = _supportedChains[params.dest];
        if (dest.length == 0) revert UnsupportedChain();

        bytes memory body = abi.encode(HyperFungibleToken.Message({
            from: abi.encodePacked(msg.sender),
            to: params.to,
            amount: params.amount,
            data: params.data
        }));

        return DispatchPost({
            dest: params.dest,
            to: dest,
            body: body,
            timeout: params.timeout,
            fee: params.relayerFee,
            payer: msg.sender
        });
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L266-290)
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

        emit Sent({
            from: msg.sender,
            to: params.to,
            dest: string(params.dest),
            amount: params.amount,
            commitment: commitment
        });
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-324)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleToken.Message memory message = abi.decode(request.body, (HyperFungibleToken.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L281-298)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleTokenUpgradeable.sol (L294-360)
```text
    function send(HyperFungibleTokenUpgradeable.SendParams calldata params) external payable whenNotPaused {
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

        emit Sent({
            from: msg.sender,
            to: params.to,
            dest: string(params.dest),
            amount: params.amount,
            commitment: commitment
        });
    }

    /**
     * @notice Handles incoming cross-chain token transfer messages
     * @dev Called by the ISMP host when a POST request is received. Verifies the source
     * address matches the configured contract for that chain, then transfers the underlying
     * ERC20 to the recipient. If calldata is present, executes it via the CallDispatcher.
     * @param incoming The incoming POST request containing the token transfer message
     */
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleTokenUpgradeable.Message memory message =
            abi.decode(request.body, (HyperFungibleTokenUpgradeable.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({from: message.from, to: beneficiary, source: string(request.source), amount: message.amount});
    }
```
