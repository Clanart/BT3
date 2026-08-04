## Title
Stale `TRANSACTION_FEES` accounting lets an `EvmHost.feeToken` migration divert IntentGateway escrow funds to the wrong token/beneficiary - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentGatewayV2`/`IntentsBase` records an order's `fees` amount under `_orders[commitment][TRANSACTION_FEES]` as a bare `uint256` with no token binding — it implicitly trusts that `IDispatcher(host()).feeToken()` will still be the same token at withdrawal time as it was at `placeOrder()` time. `EvmHost.updateHostParamsInternal` only guards against changing `feeToken` when *the Host contract itself* still holds a balance of the old token; it has no knowledge of, and does not protect, token custody held by downstream apps like `IntentGatewayV2`. If governance rotates `feeToken` while orders are in flight, `_withdraw()`'s finalize step will `safeTransfer` the recorded `fees` amount using the **new** feeToken address against the IntentGateway's actual balance — which was accumulated in the **old** feeToken (or in the new token from unrelated orders/dust). This is exactly the bug class in the external report: mutating a token address mid-distribution without accounting for balances/decimals held elsewhere in the system.

### Finding Description
- At `placeOrder()`, the fee is escrowed in whatever `feeToken()` currently is, and only the amount is stored, not the token address: [1](#0-0) 

- At settlement, `_withdraw()` re-resolves `feeToken()` from the host **at withdrawal time** and transfers the *recorded amount* using that potentially different token: [2](#0-1) 

- `EvmHost.updateHostParamsInternal` enforces the exact guard the external report recommends — but only against the Host contract's own balance, `address(this)` being the Host, not the IntentGateway: [3](#0-2) 

- This guard is explicitly documented as the caller's responsibility to have drained "all funds" from the old token before rotating, but that statement only applies to the Host's own custody, and orders escrowed by `IntentGatewayV2` are invisible to it: [4](#0-3) 

- Cross-chain orders can be open across a long window (`order.deadline`), so a `feeToken` rotation dispatched via `HostManager`/`updateHostParams` can land while many orders are still pending fill/cancel: [5](#0-4) 

Consequence when `feeToken` changes between `placeOrder()` and the corresponding `_withdraw()` (fill, cancel-from-source, or cancel-from-destination path all call `_withdraw`):
1. The IntentGateway's actual balance of the *new* feeToken is whatever unrelated orders happened to accumulate there (possibly zero, possibly nonzero from other users' post-migration order fees or protocol dust). It has no relation to the `fees` value recorded for this particular commitment.
2. If the new-token balance is insufficient, `safeTransfer` reverts, which reverts the entire `_withdraw()` call — meaning the escrowed input-token transfers earlier in the *same* function also roll back, so a legitimate fill/cancel becomes permanently stuck (fund lock) for every order that straddled the fee-token switch.
3. If the new-token balance happens to be sufficient (accumulated from other users' orders), the beneficiary of *this* commitment is paid `fees` worth of a token that has no relationship to what was actually escrowed for them — i.e., funds belonging to other orders/protocol dust are paid out to the wrong beneficiary, a direct "wrong beneficiary or amount" fund-diversion.

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant. Depending on gateway/new-token balances at the time, the result is either a broad fund-lock (all pending cross-chain orders' escrow becomes unwithdrawable) or a fund-diversion (unrelated tokens paid to a beneficiary who has no claim on them). Both are direct fund-safety impacts on the intent-settlement path, not merely a griefing/DoS on a peripheral feature.

### Likelihood Explanation
Triggering requires a `feeToken` rotation via governance (`HostManager`/`updateHostParams`) — an ordinary, expected, undocumented-as-dangerous operational action, not a compromised/malicious actor. Given `EvmHost` only checks its own zero balance (not the IntentGateway's), an operator following the documented precaution ("drain the old feeToken from the Host") will still trigger this bug, because the escrowed fee sits in `IntentGatewayV2`, not in `EvmHost`. Any order placed before the rotation and settled after it is affected — no attacker action beyond normal order placement/fill/cancel is needed, and it is entirely plausible during a routine fee-token migration.

### Recommendation
- Record the fee-token *address* alongside the `fees` amount at `placeOrder()` time (e.g., extend the `TRANSACTION_FEES` storage entry to a struct `{token, amount}`), and use that stored token address in `_withdraw()` instead of re-resolving `host().feeToken()`.
- Alternatively, extend `EvmHost.updateHostParamsInternal`'s `CannotChangeFeeToken` guard to also query and require a zero balance on all registered downstream apps (or require an explicit governance-driven drain/migration step for `IntentGatewayV2` before allowing a `feeToken` change), mirroring the original report's recommendation to disable the mutation while balances are outstanding.

### Proof of Concept
1. Governance deploys `IntentGatewayV2` pointed at `EvmHost` with `feeToken = TokenA`.
2. User calls `placeOrder(order, graffiti)` with `order.fees = 100`; `TokenA` is pulled from the user and `_orders[commitment][TRANSACTION_FEES] = 100` is recorded (see `evm/src/apps/IntentGatewayV2.sol:345-362`).
3. Before the order is filled/cancelled, governance rotates `feeToken` on `EvmHost` to `TokenB` via `HostManager.onAccept` → `updateHostParams`. The `CannotChangeFeeToken` check in `EvmHost.sol:617-621` only reverts if `EvmHost` itself holds `TokenA`; it does not see the `TokenA` sitting in `IntentGatewayV2`, so the rotation succeeds.
4. Solver fills the order (or the user cancels after deadline); the settlement path calls `_withdraw()` with `finalize = true`. It reads `fees = 100` and calls `IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, 100)` — but `host().feeToken()` now returns `TokenB` (`evm/src/apps/intentsv2/IntentsBase.sol:412-417`).
5. Outcome A: `IntentGatewayV2` holds no `TokenB` → `safeTransfer` reverts → the whole `_withdraw()` reverts → the input-token escrow release earlier in the same call is rolled back → the order's original escrow (`TokenA`/input tokens) is now stuck, since retrying hits the same revert every time.
   Outcome B: `IntentGatewayV2` holds `TokenB` balance from other orders placed after the rotation → the beneficiary receives 100 units of `TokenB` that were never associated with this commitment, taken from other users'/protocol funds.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-362)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L410-417)
```text
        }

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```

**File:** evm/src/core/EvmHost.sol (L617-621)
```text
        address oldFeeToken = feeToken();
        if (oldFeeToken != address(0) && oldFeeToken != params.feeToken) {
            uint256 balance = IERC20(oldFeeToken).balanceOf(address(this));
            if (balance != 0) revert CannotChangeFeeToken();
        }
```

**File:** evm/rust/src/host_params.rs (L144-147)
```rust
	/// The address of the fee token contract.
	/// It's important that before changing this parameter,
	/// that all funds have been drained from the previous feeToken
	pub fee_token: Option<H160>,
```

**File:** evm/src/core/HostManager.sol (L95-108)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
```
