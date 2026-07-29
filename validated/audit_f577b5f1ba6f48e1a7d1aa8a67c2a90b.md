### Title
Uncharged EVM computation during permissionless `RegisterERC20` enables cost-free block-processing DoS - (File: `x/vm/keeper/call_evm.go`, `x/erc20/keeper/evm.go`, `x/erc20/keeper/msg_server.go`)

### Summary
The Notional report describes an external contract that never reverts on a metadata-style call, causing the caller's try/catch decode logic to consume unbounded gas relative to what was actually charged. The same failure class — computation performed against attacker-controlled contract code without the cost being properly charged back to the party who triggered it — exists in Cosmos EVM's permissionless `MsgRegisterERC20` flow, which synchronously calls `name()`, `symbol()`, and `decimals()` on an attacker-supplied contract with a fixed internal gas budget, and silently drops the metering of that computation whenever the call fails.

### Finding Description
`RegisterERC20` is explicitly permissionless when `PermissionlessRegistration` is enabled: "Any account can permissionlessly register a native ERC20 contract to map to a Cosmos Coin." [1](#0-0) 

It iterates over attacker-supplied contract addresses and calls `k.registerERC20`, which eventually calls `QueryERC20`, which in turn calls `queryERC20String` for `name`/`symbol` and a direct `CallEVM` for `decimals`: [2](#0-1) [3](#0-2) 

Each of these calls goes through `CallEVM` → `CallEVMWithData`. Critically, `CallEVMWithData` ignores the `gasCap` parameter entirely and always sets `msg.GasLimit: config.DefaultGasCap` for the internal EVM execution: [4](#0-3) 

More importantly, when the internal call fails (`res.Failed()`), the function returns immediately **without** calling `ctx.GasMeter().ConsumeGas(...)` on the parent (real) transaction gas meter:
```go
res, err := k.ApplyMessage(tmpCtx, msg, nil, commit, true)
...
if res.Failed() {
    return res, errorsmod.Wrap(types.ErrVMExecution, res.VmError)
}
commitState()
ctx.GasMeter().ConsumeGas(res.GasUsed, "apply evm message")
``` [5](#0-4) 

This means an attacker can deploy a contract whose `name()`, `symbol()`, or `decimals()` function performs expensive computation (loops, hashing, etc.) up to `config.DefaultGasCap` and then reverts (or naturally runs out of the internal gas budget). Because the early return path bypasses `ConsumeGas`, none of that computation is charged against the actual `MsgRegisterERC20` transaction's Cosmos SDK gas meter — the fee payer is billed only the flat/base cost of the message itself, while every validator in the network must still execute the full, expensive EVM computation to reach the revert. `RegisterERC20` also accepts a list of addresses (`req.Erc20Addresses`) in a single message, and calls `name`, `symbol`, and `decimals` (3 uncharged/undercharged EVM executions) per address, multiplying the effect.

### Impact Explanation
This is a resource-exhaustion / gas-metering bypass reachable by any unprivileged account (no governance or admin privilege required) through an ordinary transaction (`MsgRegisterERC20`). By decoupling real EVM computation cost from the gas actually billed to the submitter, an attacker can force disproportionate CPU consumption across all validators for a transaction with a trivially low declared/paid gas cost. Submitted repeatedly (and batched via multiple addresses per message, and multiple messages per block up to the block gas limit as perceived by the *metered* — not actual — gas), this can degrade block production time chain-wide, i.e., a chain-halt/liveness-DoS vector triggerable by an ordinary, unprivileged transaction flow, matching the "chain halt ... that an unprivileged user can trigger through ordinary transaction ... flow" critical impact category.

### Likelihood Explanation
Likelihood is high given: (1) `PermissionlessRegistration` is a supported, documented mode of the module (not merely a test flag), (2) no special contract compliance is required — an attacker fully controls the bytecode of the registered contract's `name`/`symbol`/`decimals` implementations, (3) the vulnerable code path (`CallEVMWithData`'s failure-branch skipping `ConsumeGas`) is unconditionally reached for any failing internal call, and (4) it requires no relayer, validator, or governance cooperation.

### Recommendation
- In `CallEVMWithData` (`x/vm/keeper/call_evm.go`), always charge the parent context's gas meter with the actual EVM gas used (`res.GasUsed`) regardless of whether the internal call succeeded or failed, before returning the error.
- Honor the `gasCap` parameter that is currently ignored, so callers like `QueryERC20`/`queryERC20String` can bound the EVM gas used for read-only metadata probing to a small, purpose-appropriate limit instead of `config.DefaultGasCap`.
- Consider requiring `MsgRegisterERC20`'s declared/paid gas to cover the worst-case cost of `len(Erc20Addresses) * 3 * DefaultGasCap`, or cap the number of addresses processed per message and per name/symbol/decimals probe.

### Proof of Concept
Conceptual PoC (not executed, based on code trace):
1. Deploy a malicious contract `Evil` whose `decimals()` (or `name()`/`symbol()`) function runs a loop performing expensive operations (e.g., repeated `keccak256`) consuming close to `config.DefaultGasCap` gas, then executes `revert()`.
2. Submit `MsgRegisterERC20{ Erc20Addresses: [Evil address] }` from any unprivileged account (assuming `PermissionlessRegistration=true`).
3. `RegisterERC20` → `registerERC20` → `QueryERC20` → `queryERC20String`/`CallEVM` invokes `Evil.decimals()` with `GasLimit = config.DefaultGasCap`; the call fails/reverts inside the EVM.
4. `CallEVMWithData` returns early on `res.Failed()` without calling `ctx.GasMeter().ConsumeGas(res.GasUsed, ...)`, so the real Cosmos SDK gas meter for the transaction is never charged the near-`DefaultGasCap` EVM computation that was actually performed by every validator.
5. The transaction reverts cheaply from the fee payer's perspective while all nodes performed maximal computation — repeatable across many transactions/blocks with negligible cost to the attacker.

Note: I could not directly read the exact numeric value of `config.DefaultGasCap` (found in `server/config/config.go`) within the available iterations; per the index limitations, a Devin session with full repository access would be needed to confirm the exact magnitude and to build and execute a concrete runnable PoC/test against the `x/erc20` and `x/vm` keeper test suites.

### Citations

**File:** x/erc20/keeper/msg_server.go (L324-345)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}
```

**File:** x/erc20/keeper/evm.go (L67-100)
```go
// QueryERC20 returns the data of a deployed ERC20 contract
func (k Keeper) QueryERC20(
	ctx sdk.Context,
	contract common.Address,
) (types.ERC20Data, error) {
	erc20 := contracts.ERC20MinterBurnerDecimalsContract.ABI

	// Name - with fallback support for bytes32
	name, err := k.queryERC20String(ctx, erc20, contract, "name")
	if err != nil {
		return types.ERC20Data{}, err
	}

	// Symbol - with fallback support for bytes32
	symbol, err := k.queryERC20String(ctx, erc20, contract, "symbol")
	if err != nil {
		return types.ERC20Data{}, err
	}

	// Decimals - standard uint8, no fallback needed
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, false, nil, "decimals")
	if err != nil {
		return types.ERC20Data{}, err
	}

	var decimalRes types.ERC20Uint8Response
	if err := erc20.UnpackIntoInterface(&decimalRes, "decimals", res.Ret); err != nil {
		return types.ERC20Data{}, errorsmod.Wrapf(
			types.ErrABIUnpack, "failed to unpack decimals: %s", err.Error(),
		)
	}

	return types.NewERC20Data(name, symbol, decimalRes.Value), nil
}
```

**File:** x/erc20/keeper/evm.go (L102-135)
```go
// queryERC20String attempts to query an ERC20 string field with fallback to bytes32
func (k Keeper) queryERC20String(
	ctx sdk.Context,
	erc20 abi.ABI,
	contract common.Address,
	method string,
) (string, error) {
	// 1) Call into the EVM
	res, err := k.evmKeeper.CallEVM(ctx, erc20, types.ModuleAddress, contract, false, nil, method)
	if err != nil {
		return "", err
	}

	// 2) First try to unpack as a normal ABI “string”
	var strResp types.ERC20StringResponse
	if err := erc20.UnpackIntoInterface(&strResp, method, res.Ret); err == nil {
		return strResp.Value, nil
	}

	// 3) Fallback: if we got exactly 32 bytes back, treat it as bytes32
	if len(res.Ret) == 32 {
		var b [32]byte
		copy(b[:], res.Ret)
		return utils.Bytes32ToString(b), nil
	}

	// 4) Otherwise it really is neither a string nor a 32‐byte static, so error
	return "", errorsmod.Wrapf(
		types.ErrABIUnpack,
		"failed to unpack %s as both string and raw bytes32 (len=%d)",
		method,
		len(res.Ret),
	)
}
```

**File:** x/vm/keeper/call_evm.go (L48-93)
```go
// CallEVMWithData performs a smart contract method call using contract data.
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

	// Use a cache context so that a reverting EVM call does not corrupt the
	// parent gas meter. On success we commit the cache and charge the actual
	// gas used; on revert we discard the cache and leave the parent meter
	// untouched (matching DerivedEVMCallWithData semantics).
	tmpCtx, commitState := ctx.CacheContext()
	res, err := k.ApplyMessage(tmpCtx, msg, nil, commit, true)
	if err != nil {
		return nil, err
	}

	if res.Failed() {
		return res, errorsmod.Wrap(types.ErrVMExecution, res.VmError)
	}

	commitState()
	ctx.GasMeter().ConsumeGas(res.GasUsed, "apply evm message")

	return res, nil
}
```
