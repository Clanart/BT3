Confirmed: `CallDispatcher` is deployed once per chain (`config.testnet.toml` — `CALL_DISPATCHER = "0x2B33..."` shared across `HOST_MANAGER`, `INTENT_GATEWAY_V2`, and every `HyperFungibleToken`/`WrappedHyperFungibleToken` deployment) and is passed as `dispatcher` into `IntentGatewayV2`, `HyperFungibleToken`, `WrappedHyperFungibleToken`, all pointing at the **same instance**. This confirms the shared-singleton custody role and supports the finding below.

### Title
Unrestricted `CallDispatcher.dispatch()` allows anyone to sweep residual funds left in the shared dispatcher during cross-chain calldata execution - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher.dispatch()` has no access control at all, exactly mirroring the reported `BancorSwapper.trade()` pattern — a public, unrestricted entrypoint on a contract that legitimately holds funds in transit. Unlike `trade()`, this function is not "unused": it is the single shared execution relay used by `IntentGatewayV2`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` across every chain, and it routinely holds native ETH/ERC20 balances mid-flow. Because `dispatch()` can be called by any address, any balance sitting in the `CallDispatcher` at any time can be permissionlessly redirected to an attacker.

### Finding Description
`CallDispatcher.dispatch()` executes arbitrary attacker-supplied `Call[]` against arbitrary targets using the contract's own balance, with zero `msg.sender` restriction: [1](#0-0) 

The `CallDispatcher` is deployed once and shared by multiple production apps (`IntentGatewayV2`, `HyperFungibleToken`, `WrappedHyperFungibleToken`) as configured in deployment scripts and `config.testnet.toml`: [2](#0-1) [3](#0-2) 

In `IntentsBase._execute`, the protocol acknowledges that dispatching arbitrary calls through this contract leaves "dust" (residual token/ETH balances) and explicitly enumerates a sweep step to recover it: [4](#0-3) 

However, in `HyperFungibleToken.onAccept` / `WrappedHyperFungibleToken.onAccept` (the cross-chain "Calldata Execution" feature, where tokens are unlocked directly to the `CallDispatcher` address and then `message.data` is dispatched), there is **no equivalent sweep-back logic**: [5](#0-4) [6](#0-5) 

The documented pattern explicitly instructs integrators to set `to = CALL_DISPATCHER` so it "receives the unwrapped ETH" before executing `Call.value`-forwarding calls, confirming the dispatcher regularly and intentionally holds real, spendable balances: [7](#0-6) 

Because `dispatch()` is unrestricted, any leftover balance — from partial consumption of unlocked funds, rounding, a Call array that doesn't spend 100% of the unwrapped/unlocked amount, or direct ETH sent via the contract's payable `receive()` — becomes a public bounty: any address can call `dispatch()` directly with a `Call{to: attacker, value: <balance>, data: ""}` (or an ERC20 `transfer` call) and take it, with no relation to the original order/message beneficiary.

### Impact Explanation
This breaks the "bridged assets ... must move exactly once and only to the rightful beneficiary and amount" invariant. Funds that legitimately belong to a cross-chain transfer's recipient (dust from the calldata-execution path) or to a solver/protocol (residual balances not captured by a sweep) can be diverted to an unrelated third party. Because the `CallDispatcher` is a shared singleton across every app that uses the "calldata execution" feature, the blast radius extends to all current and future integrators pointing at that one deployed address per chain.

### Likelihood Explanation
No privileged actor, malicious relayer, or prover is required — the attacker only needs to monitor the shared `CallDispatcher`'s balance (a single public address per chain) and race a plain transaction calling `dispatch()` whenever a nonzero balance appears, e.g., after any cross-chain "Calldata Execution" delivery whose attached calls don't consume the entire unlocked/unwrapped amount. Given the contract is a permanent, publicly known, and widely reused component, this is a realistic and repeatable condition rather than a purely theoretical one.

### Recommendation
Add access control to `CallDispatcher.dispatch()` restricting callers to an allow-list of authorized apps (e.g., the configuring `IntentGatewayV2`/`HyperFungibleToken`/`WrappedHyperFungibleToken` instances), or make dispatch atomic-and-ephemeral (e.g., require the caller to also supply and immediately execute a full sweep of any residual balance back to a designated owner within the same call), and add the same sweep-on-completion behavior found in `IntentsBase._execute` to the `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata-execution paths so no address-owned dust is left in a permissionless contract.

### Proof of Concept
1. A cross-chain transfer using the "Calldata Execution" feature completes: `WrappedHyperFungibleToken.onAccept` unwraps WETH and unlocks `amount` to `CALL_DISPATCHER`, then calls `dispatch(message.data)` where `message.data` decodes to `Call[]` that forwards less than the full unwrapped ETH balance to a downstream router (e.g., a swap that doesn't use 100% of `Call.value` due to slippage bounds or a fixed swap amount, per the documented `swapETHForExactTokens` example).
2. Some ETH remains on the `CallDispatcher` contract's balance after the transaction.
3. Any attacker (unrelated to the transfer) submits a direct transaction: `CallDispatcher.dispatch(abi.encode([Call({to: attacker, value: residualBalance, data: ""})]))`.
4. Since `dispatch()` has no `msg.sender` check, the call succeeds and the residual ETH is transferred to the attacker, who was never the intended beneficiary of the original cross-chain message.

Note: I could not find a sweep step or balance-zeroing guarantee anywhere in the `HyperFungibleToken`/`WrappedHyperFungibleToken` calldata-execution path within the indexed code, so I cannot confirm whether dust is fully eliminated by strict amount-matching in all integrator configurations — this would need to be verified against the exact `Call[]` payloads used in production deployments, which are external, off-chain-constructed data not fully visible in this index.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-61)
```text
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
```

**File:** evm/config.testnet.toml (L1-16)
```text
[11155111]
endpoint_url = "${SEPOLIA_RPC_URL}"

[11155111.address]
HOST = "0x9AA003594d59C62EE17A73A569Fd7B1DbdBd71E1"
ECDSA_BEEFY = "0x579ee6F2e4a73cc17E701Dd442bcA275F953f789"
SP1_BEEFY = "0x4C4b6e888e552bAbaE04dECf2BE30Ca25629a350"
CONSENSUS_ROUTER = "0x3ab707223E81e18BA7b9Fb64a15308D65c996461"
HANDLER_V2 = "0xb679e92aDE0A130C8Caf9620ce2AaA71547BC0e1"
TOKEN_FAUCET = "0xcb00f5b86aac5E2fdCa9dC7f34d9bFe00b967c18"
FEE_TOKEN = "0xBE97E73126D66188d72fbF99029126D0340a7f18"
CALL_DISPATCHER = "0x2B332088275Bc9E3C26D81B2975de2483320C181"
BANDWIDTH_MANAGER = "0xfE6d81417CFe618ACAA34Df23376C738900290de"
SP1_VERIFIER = "0xe46f1D4A1fC2130C3a3f3f5f78432774D28d249D"
HOST_MANAGER = "0xa7273Cb04E99978CF5c0A935e92EC95F09e9D44a"

```

**File:** evm/script/DeployHFT.s.sol (L9-26)
```text
contract DeployHFT is BaseScript {
    function deploy() internal override {
        string memory name = vm.envString("HFT_NAME");
        string memory symbol = vm.envString("HFT_SYMBOL");

        CallDispatcher dispatcher = new CallDispatcher{salt: salt}();
        HyperFungibleToken hft = new HyperFungibleToken{salt: salt}(name, symbol, admin);

        hft.configure(HyperFungibleToken.ConfigOptions({
            host: HOST_ADDRESS,
            dispatcher: address(dispatcher)
        }));

        vm.stopBroadcast();
        console.log("=== HFT Deployment ===");
        console.log("HyperFungibleToken:", address(hft));
        console.log("CallDispatcher:", address(dispatcher));
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-467)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-328)
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

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }
```

**File:** sdk/packages/core/contracts/interfaces/ICallDispatcher.sol (L32-37)
```text
interface ICallDispatcher {
    /*
     * @dev Dispatch the encoded call(s)
     */
    function dispatch(bytes memory params) external;
}
```

**File:** docs/content/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token.mdx (L164-203)
```text
When `isWeth = true`, the WrappedHFT unwraps WETH to native ETH on receive. This example bridges WETH back to the home chain, where it's unwrapped to native ETH and swapped for an exact amount of USDC via UniswapV2. The `Call.value` field forwards the native ETH to the router — demonstrating that the `CallDispatcher` can hold and forward native tokens:

```solidity lineNumbers
import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";

address[] memory path = new address[](2);
path[0] = WETH;
path[1] = USDC;

Call[] memory calls = new Call[](1);

// Swap native ETH → exact USDC via UniswapV2
// The CallDispatcher holds the unwrapped ETH and forwards it via Call.value
calls[0] = Call({
    to: UNISWAP_V2_ROUTER,
    // forward the native ETH to the router
    value: amount,
    data: abi.encodeWithSelector(
        IUniswapV2Router02.swapETHForExactTokens.selector,
        usdcAmountOut,
        path,
        recipientAddress,
        block.timestamp
    )
});

IHyperFungibleToken(address(wrapper)).send{value: nativeFee}(
    IHyperFungibleToken.SendParams({
        dest: StateMachine.evm(1),
        // unlock to the CallDispatcher so it receives the unwrapped ETH
        to: abi.encodePacked(CALL_DISPATCHER),
        amount: amount,
        timeout: 3600,
        relayerFee: relayerFee,
        data: abi.encode(calls)
    })
);
```

Tokens are unlocked (or unwrapped for WETH) to `to` first, then the `CallDispatcher` executes each call in sequence. Setting `to` to the `CallDispatcher` address ensures the dispatcher holds the tokens (or native ETH) so subsequent calls can spend them via `Call.value` or ERC20 transfers.
```
