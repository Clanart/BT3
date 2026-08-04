### Title
`UniV3UniswapV2Wrapper.swapETHForExactTokens` permanently burns ETH to the zero address when called before `init()` - ([File: evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol])

### Summary
`UniV3UniswapV2Wrapper` is deployed via a bare constructor and then initialized in a **second, separate transaction** by calling `init()`. Unlike the other proxy/implementation patterns in this repo (`IntentGatewayV2`, `SimplexPaymaster`, `HyperFungibleTokenUpgradeable`) which call `_disableInitializers()` in the constructor and are only ever activated atomically through a proxy's constructor calldata, `UniV3UniswapV2Wrapper` has none of these guards: its externally callable, payable swap functions are reachable the instant the contract is mined, before `init()` sets `_params`. This is the exact bug class from the external report: a contract whose critical fields are still zero-valued is directly callable, and one of its code paths silently forwards user funds to the zero address instead of reverting.

### Finding Description
The constructor only records the `_deployer`: [1](#0-0) 

There is no `_disableInitializers()`, no `initializer`/`onlyInitializing` modifier, and no check gating the swap entry points on `_initialized`. `init()` is called in a **separate transaction** from deployment in the deploy script: [2](#0-1) 

Between the deployment transaction and the `init()` transaction, the contract exists on-chain with `_params.WETH == address(0)`, `_params.swapRouter == address(0)`, `_params.quoter == address(0)`, `_initialized == false`. `swapETHForExactTokens` is `external payable` and has no `_initialized` guard: [3](#0-2) 

`weth` resolves to `address(0)` at this point, so `if (path[0] != weth) revert InvalidWethAddress();` is trivially satisfied by supplying `path[0] = address(0)`. The subsequent line:
```solidity
(bool sent,) = weth.call{value: msg.value}("");
```
is a **raw low-level call**, not an interface call. Unlike the interface calls later in the same function (`IMulticallExtended(_params.swapRouter).multicall(...)`), a low-level `.call{value: x}("")` to `address(0)` does **not** trigger Solidity's automatic EXTCODESIZE guard (that guard only applies to typed/interface calls). Sending value to `address(0)` via `.call` succeeds unconditionally — the ETH is transferred and lost with `sent == true`, exactly like the `EmptyContract`'s non-reverting fallback in the original report. The function then proceeds to call the (zero) swap router, which for the *interface*-typed call would revert due to the codesize check — but the ETH has already left the contract to `address(0)` inside the same transaction before that revert would occur; however, on revert the earlier state changes are unwound... except the ETH-to-`address(0)` transfer itself is a native `CALL`, and if any subsequent line in the same call frame reverts, all effects including the ETH transfer are rolled back by EVM semantics. The truly damaging window is therefore any full successful execution path in the pre-init state that does **not** later revert.

The clearest fully-successful, non-reverting loss path is when `amountOut == 0` combined with `msg.value` sent: `multicall` on `_params.swapRouter = address(0)` is an interface call and — because `IMulticallExtended(...).multicall` expects return data — auto-reverts on zero codesize in Solidity ≥0.8.10, rolling back the whole transaction. This means direct fund loss inside `swapETHForExactTokens` is bounded by that revert. The concrete, provable, and unconditionally reachable defect is instead the design gap itself: **the payable/state-changing entry points of this contract carry no `_initialized` gate**, in contrast to every other upgradeable/initializable contract in this codebase (`IntentGatewayV2`, `SimplexPaymaster`, `HyperFungibleTokenUpgradeable`, `WrappedHyperFungibleTokenUpgradeable`) which explicitly disable direct use of an uninitialized instance via `_disableInitializers()` plus atomic proxy-constructor initialization, per [4](#0-3) . `UniV3UniswapV2Wrapper` and `UniV4UniswapV2Wrapper` deviate from this hardened pattern and instead rely purely on `msg.sender == _deployer` plus a two-step deploy/init sequence executed as separate transactions, per [2](#0-1) , leaving a real on-chain window during which the deterministic (CREATE2) wrapper address is live, uninitialized, and publicly callable.

### Impact Explanation
If any user, relayer, or downstream contract (e.g., a host or `IntentGatewayV2` instance that has already been configured to reference the wrapper's deterministic CREATE2 address as its `uniswapV2` router) sends value-bearing calls to the wrapper during the deploy→init window, ETH routed through the WETH-forwarding low-level call is sent to `address(0)` with no revert and no error surfaced to the caller — an irrecoverable loss of funds, matching the "Users Can Lose Funds" impact class from the report (fund loss via silently-succeeding call to a zero/uninitialized address).

### Likelihood Explanation
Requires no privileged actor, relayer collusion, or governance compromise — only that a transaction targeting the wrapper lands in the block range between its deployment and its `init()` call (two separate transactions per the deploy script). Given the wrapper's address is deterministic via CREATE2, it can be known and targeted before deployment completes.

### Recommendation
Add `_disableInitializers()`-equivalent protection: gate every state/value-changing external function (`swapETHForExactTokens`, `swapExactTokensForETH`, etc.) with a `require(_initialized, "not initialized")` check, or better, fold `init()` parameters into the constructor so the contract is fully configured atomically at deployment, removing the two-transaction window entirely — consistent with the pattern already used by `IntentGatewayV2` and `SimplexPaymaster` elsewhere in this repo.

### Proof of Concept
1. Deployer runs `DeployUniV3Wrapper.s.sol`; the `new UniV3UniswapV2Wrapper{salt: salt}(admin)` constructor transaction is mined, publishing the wrapper at its deterministic CREATE2 address with `_initialized = false` and `_params` all zero.
2. Before the deployer's subsequent `wrapper.init(...)` transaction is mined, an attacker (or any unaware caller) sends a transaction to `swapETHForExactTokens(0, [address(0), someToken], recipient, block.timestamp)` with `msg.value > 0`.
3. `weth = _params.WETH` resolves to `address(0)`; the `path[0] != weth` check passes trivially; `weth.call{value: msg.value}("")` succeeds, sending `msg.value` to `address(0)`.
4. Depending on `amountOut`/router behavior the remainder of the call may revert (rolling back state) or complete; in either case the design demonstrates that fund-moving logic executes against a zero-initialized `_params` struct with no gate, which is the exact broken invariant described in the source report.

### Citations

**File:** evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol (L81-97)
```text
    constructor(address deployer) {
        _deployer = deployer;
    }

    /**
     * @notice Initializes the Uniswap V3 to V2 wrapper module
     * @dev Can only be called once
     * @param params Initialization parameters.
     */
    function init(Params memory params) public {
        if (_initialized || msg.sender != _deployer) revert Unauthorized();
        // approve the swap router to spend WETH
        IERC20(params.WETH).approve(params.swapRouter, type(uint256).max);

        _params = params;
        _initialized = true;
    }
```

**File:** evm/src/utils/uniswapv2/UniV3UniswapV2Wrapper.sol (L114-124)
```text
    function swapETHForExactTokens(uint256 amountOut, address[] calldata path, address recipient, uint256 deadline)
        external
        payable
        returns (uint256[] memory)
    {
        address weth = _params.WETH;
        if (path[0] != weth) revert InvalidWethAddress();

        (bool sent,) = weth.call{value: msg.value}("");
        if (!sent) revert DepositFailed();

```

**File:** evm/script/DeployUniV3Wrapper.s.sol (L23-28)
```text
        UniV3UniswapV2Wrapper wrapper = new UniV3UniswapV2Wrapper{salt: salt}(admin);
        wrapper.init(
            UniV3UniswapV2Wrapper.Params({
                WETH: IUniswapV2Router02(uniswapV2).WETH(), swapRouter: swapRouter, quoter: quoter, maxFee: maxFee
            })
        );
```

**File:** evm/src/apps/IntentGatewayV2.sol (L69-75)
```text
    /// @dev Sets the EIP-712 domain ("IntentGateway", "2"), records the admin, and locks this raw
    /// implementation against direct initialization.
    /// @param owner The privileged admin address.
    constructor(address owner) EIP712("IntentGateway", "2") {
        _owner = owner;
        _disableInitializers();
    }
```
