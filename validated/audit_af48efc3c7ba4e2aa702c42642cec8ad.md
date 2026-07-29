Confirmed: `CallEVMWithData` in `x/vm/keeper/call_evm.go` accepts a `gasCap *big.Int` parameter but never uses it to set the executed message's gas limit — the `core.Message.GasLimit` field is hardcoded to `config.DefaultGasCap` regardless of the caller-supplied cap. [1](#0-0) 

### Title
IBC EVM callback gas cap silently ignored, allowing unbounded EVM computation from a declared minimal gas budget - ([File: x/vm/keeper/call_evm.go])

### Summary
This is the closest reachable analog to the reported native `transfer()` gas-stipend bug class in this codebase: instead of a fixed *too-small* gas stipend, the `x/ibc/callbacks` module relies on `CallEVMWithData`'s `gasCap` parameter to bound EVM callback execution to the attacker-controlled but validator-agreed `gas_limit` declared in the ICS-20 packet memo. That parameter is silently discarded, and the actual EVM message executes with `config.DefaultGasCap` instead.

### Finding Description
The `x/ibc/callbacks` keeper (`x/ibc/callbacks/keeper/keeper.go`) implements `IBCReceivePacketCallback`, `IBCOnAcknowledgementPacketCallback`, and `IBCOnTimeoutPacketCallback`. Each derives a `remainingGas`/gas cap from `cbData.CommitGasLimit` (attacker-controlled, taken from the packet's `memo.{dest,src}_callback.gas_limit` field), and passes it into `k.evmKeeper.CallEVM(...)` / `CallEVMWithData(...)` as the `gasCap` argument, expecting the EVM execution itself to be bounded by that value: [2](#0-1) 

However, `CallEVMWithData` ignores this argument entirely and instead sets:
```go
msg := core.Message{
    ...
    GasLimit:   config.DefaultGasCap,
    ...
}
``` [3](#0-2) 

The intended enforcement — comparing `res.GasUsed` against the caller's declared cap after execution and returning `ErrOutOfGas` — only happens post-hoc: [4](#0-3) 

This means the actual EVM computation performed for a single IBC callback invocation is **not bounded by the declared `gas_limit`** during execution; it is bounded only by `config.DefaultGasCap` (a large chain-wide constant), and the caller's tiny declared `gas_limit` is only checked afterward, at which point the (potentially very large) real computational work has already been done, only to have its side effects discarded and the packet processing rejected as `ErrOutOfGas`.

### Impact Explanation
An unprivileged IBC packet sender (or contract deployer whose address is used as a destination/source callback target) can craft a packet whose memo declares a minimal `gas_limit` (e.g. `1`), while pointing the callback at a contract engineered to consume computation up to `config.DefaultGasCap`. Because the EVM message's actual `GasLimit` field is hardcoded rather than derived from the declared cap, every relayer submitting such a packet forces full nodes to execute up to `DefaultGasCap` worth of EVM opcodes per callback, regardless of the SDK-level gas budget the transaction/packet processing framework believed it was allotting. This decouples the cost accounted for by IBC-Go's callbacks middleware (which reserves/limits gas based on the declared `gas_limit`) from the real computational cost incurred by validators, enabling a computation-amplification/DoS vector: an attacker can cheaply flood relayed packets that each force disproportionately large EVM execution work on every validating node, well beyond what the packet's own gas accounting reflects. This can degrade block production time across the network — a chain-halt/liveness-adjacent impact triggerable by an ordinary, unprivileged relayed IBC transaction.

### Likelihood Explanation
The trigger requires no privileged access: any account can send an ICS-20 transfer with a `dest_callback`/`src_callback` memo pointing to an attacker-deployed contract, and any relayer will submit it. The bug is a straightforward parameter-plumbing defect (`gasCap` unused in `CallEVMWithData`) so it will be hit on every callback invocation, not just adversarial ones — the discrepancy between declared and actual gas ceiling is always present, and is made attacker-exploitable via the attacker's own EVM contract logic controlling how much of the (essentially unbounded relative to declared) headroom is actually consumed.

### Recommendation
Wire the `gasCap` parameter through to `core.Message.GasLimit` in `CallEVMWithData` (`x/vm/keeper/call_evm.go`), e.g. `GasLimit: gasCap.Uint64()` (with a sane minimum/maximum clamp against `config.DefaultGasCap`), so that EVM execution is actually bounded by the caller-declared gas ceiling instead of relying purely on post-execution accounting. Audit all other callers of `CallEVM`/`CallEVMWithData` that pass a meaningful `gasCap` to confirm they are not similarly relying on an unenforced parameter.

### Proof of Concept
1. Deploy a contract on the destination/source chain whose entrypoint (e.g. `onPacketAcknowledgement`/callback function invoked via `dest_callback`) performs a gas-heavy loop (e.g. large storage writes) sized to consume close to `config.DefaultGasCap`.
2. Initiate an ICS-20 transfer with memo:
```json
{ "dest_callback": { "address": "<attacker_contract>", "gas_limit": "1", "calldata": "..." } }
```
3. On `OnRecvPacket`, `IBCReceivePacketCallback` computes `remainingGas` from the declared `gas_limit` (effectively minimal) and calls `k.evmKeeper.CallEVMWithData(cachedCtx, ..., remainingGas)`.
4. Inside `CallEVMWithData`, the message actually executes with `GasLimit: config.DefaultGasCap`, allowing the attacker's contract to run its full heavy loop rather than aborting immediately for exceeding the declared 1-gas budget.
5. Only after full execution does `ctx.GasMeter().ConsumeGas(res.GasUsed, ...)` detect the overage and return `ErrOutOfGas`, but the computational cost has already been incurred by the node — repeatable at negligible cost per packet by the attacker.

Note: I was unable to fully verify whether IBC-Go's own `ProcessCallback`/callbacks middleware imposes an independent execution-time or gas ceiling on top of this before invoking `ContractKeeper`, which could partially mitigate real-world severity; confirming that requires deeper review of the vendored `ibc-go/v10/modules/apps/callbacks` middleware, which is outside this repository's indexed code.

### Citations

**File:** x/vm/keeper/call_evm.go (L49-73)
```go
func (k Keeper) CallEVMWithData(
	ctx sdk.Context,
	from common.Address,
	contract *common.Address,
	data []byte,
	commit bool,
	gasCap *big.Int,
) (*types.MsgEthereumTxResponse, error) {
	nonce, err := k.accountKeeper.GetSequence(ctx, from.Bytes())
	if err != nil {
		return nil, err
	}

	msg := core.Message{
		From:       from,
		To:         contract,
		Nonce:      nonce,
		Value:      big.NewInt(0),
		GasLimit:   config.DefaultGasCap,
		GasPrice:   big.NewInt(0),
		GasTipCap:  big.NewInt(0),
		GasFeeCap:  big.NewInt(0),
		Data:       data,
		AccessList: ethtypes.AccessList{},
	}
```

**File:** x/ibc/callbacks/keeper/keeper.go (L187-202)
```go
	remainingGas := math.NewIntFromUint64(cachedCtx.GasMeter().GasRemaining()).BigInt()

	// Call the EVM with the remaining gas as the maximum gas limit.
	// Up to now, the remaining gas is equal to the callback gas limit set by the user.
	// NOTE: use the cached ctx for the EVM calls.
	res, err := k.evmKeeper.CallEVM(cachedCtx, erc20.ABI, receiverHex, tokenPair.GetERC20Contract(), true, remainingGas, "approve", contractAddr, amountInt.BigInt())
	if err != nil {
		return errorsmod.Wrapf(types.ErrAllowanceFailed, "failed to set allowance: %v", err)
	}

	// Consume the actual used gas on the original callback context.
	ctx.GasMeter().ConsumeGas(res.GasUsed, "callback allowance")
	remainingGas = remainingGas.Sub(remainingGas, math.NewIntFromUint64(res.GasUsed).BigInt())
	if ctx.GasMeter().IsOutOfGas() || remainingGas.Cmp(big.NewInt(0)) < 0 {
		return errorsmod.Wrapf(types.ErrOutOfGas, "out of gas")
	}
```
