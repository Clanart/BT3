### Title
Missing EOA Sender Validation in `eth_simulateV1` Validation Mode — (`x/evm/keeper/simulate.go`)

### Summary
The `Simulator.applyCall` function, which executes calls in `eth_simulateV1` validation mode, omits the EOA (Externally Owned Account) sender check that the ante handler enforces for real transactions in pre-Prague forks. As a result, `eth_simulateV1` with `validation=true` incorrectly reports that transactions originating from contract addresses would succeed, when the ante handler would reject them outright.

### Finding Description

**Ante handler path — validation present:**

`VerifyEthAccount` in `ante/eth.go` enforces that the sender must be an EOA when the chain is not yet Prague:

```go
if !rules.IsPrague {
    if acct.IsContract() {
        return errorsmod.Wrapf(errortypes.ErrInvalidType,
            "the sender is not EOA: address %s, codeHash <%s>", fromAddr, acct.CodeHash)
    }
}
``` [1](#0-0) 

This check runs for every real `MsgEthereumTx` through `newEthAnteHandler`: [2](#0-1) 

**`eth_simulateV1` validation path — validation absent:**

`Simulator.applyCall` in `x/evm/keeper/simulate.go` performs nonce, fee-cap, and balance checks when `sim.validate == true`, but never checks whether the sender is an EOA:

```go
if sim.validate {
    // nonce checks (lines 360-372)
    // London fee checks (lines 374-379)
}
// balance check (always runs)
``` [3](#0-2) 

There is no `core.ErrSenderNoEOA` / `acct.IsContract()` guard anywhere in `applyCall`. The function proceeds directly to `evm.Call` or `evm.Create` with the contract address as sender. [4](#0-3) 

**Intent to handle the error exists but is never triggered:**

`TxValidationError` in `rpc/types/simulate_errors.go` defines `ErrCodeSenderIsNotEOA = -38024` and maps `core.ErrSenderNoEOA` to it, confirming the design intent that validation mode should surface this error: [5](#0-4) 

Because `applyCall` never returns `core.ErrSenderNoEOA`, this error code is dead code in the validation path.

**Structural parallel to the external report:**

| | External report | Ethermint analog |
|---|---|---|
| Path with validation | `swap()` checks wallet ownership | Ante handler checks EOA status |
| Path without validation | `buy()` skips wallet check | `applyCall` (validation mode) skips EOA check |
| Bypass | Buy tokens with unregistered wallet | Simulate tx from contract address as if it would succeed |

### Impact Explanation

`eth_simulateV1` with `validation=true` is the public JSON-RPC `eth_simulateV1` endpoint. Its explicit purpose is to predict whether a sequence of transactions would succeed if submitted to the chain. When a contract address is used as `from`, the simulation returns `status: 0x1` (success) for pre-Prague forks, while the real ante handler would reject the transaction with `"the sender is not EOA"`. This causes the public simulation RPC path to feed incorrect consensus-critical data — specifically, a false positive execution outcome — to any caller that relies on simulation results to decide whether to broadcast a transaction. This matches the allowed High impact: *"Public JSON-RPC, gRPC, simulation, tracing, receipt/log, or indexer path feeds incorrect consensus-critical data into transaction execution."*

### Likelihood Explanation

The `eth_simulateV1` endpoint is publicly reachable by any unprivileged caller. No special permissions, keys, or validator access are required. Any user or dApp that calls `eth_simulateV1` with `validation=true` and a contract address as `from` will trigger the discrepancy. Pre-Prague forks (the majority of deployed Ethermint chains) are affected.

### Recommendation

Add an EOA check inside `applyCall` when `sim.validate == true` and the chain rules are pre-Prague, mirroring the ante handler's `VerifyEthAccount` logic:

```go
if sim.validate && !rules.IsPrague {
    code := sim.state.GetCode(sender)
    if _, isDelegation := ethtypes.ParseDelegation(code); len(code) != 0 && !isDelegation {
        return applyCallResult{}, fmt.Errorf("%w: address %v",
            core.ErrSenderNoEOA, sender.Hex())
    }
}
```

This ensures `applyCall` in validation mode returns `core.ErrSenderNoEOA` (already mapped to `ErrCodeSenderIsNotEOA = -38024` in `TxValidationError`) for contract senders, making simulation results consistent with real ante handler enforcement.

### Proof of Concept

1. Deploy a contract at address `0xC0DE` on a pre-Prague Ethermint chain.
2. Call `eth_simulateV1` with `validation: true` and `from: "0xC0DE"`, `to: <any>`, `gas: 21000`, `nonce: 0`.
3. `Simulator.applyCall` skips the EOA check, passes the balance/nonce/fee checks, and returns `status: 0x1`.
4. The caller, trusting the simulation, broadcasts the same transaction via `eth_sendRawTransaction`.
5. The ante handler's `VerifyEthAccount` rejects it: `"the sender is not EOA: address 0xC0DE, codeHash <...>"`.
6. The transaction fails; the simulation result was incorrect.

The discrepancy is rooted in `applyCall` at `x/evm/keeper/simulate.go` lines 359–399 (validation checks) and lines 438–451 (execution), where no EOA guard exists, versus `ante/eth.go` lines 93–99 where the guard is enforced for real transactions.

### Citations

**File:** ante/eth.go (L93-99)
```go
		if !rules.IsPrague {
			if acct.IsContract() {
				fromAddr := common.BytesToAddress(from)
				return errorsmod.Wrapf(errortypes.ErrInvalidType,
					"the sender is not EOA: address %s, codeHash <%s>", fromAddr, acct.CodeHash)
			}
		}
```

**File:** evmd/ante/handler_options.go (L131-133)
```go
		if err := evmante.VerifyEthAccount(ctx, tx, options.EvmKeeper, evmDenom, accountGetter, rules); err != nil {
			return ctx, err
		}
```

**File:** x/evm/keeper/simulate.go (L359-399)
```go
	if sim.validate {
		if msg.Nonce == math.MaxUint64 {
			return applyCallResult{}, fmt.Errorf("%w: address %v, nonce: %d",
				core.ErrNonceMax, sender.Hex(), msg.Nonce)
		}
		stateNonce := sim.state.GetNonce(sender)
		if msg.Nonce < stateNonce {
			return applyCallResult{}, fmt.Errorf("%w: address %v, tx: %d state: %d",
				core.ErrNonceTooLow, sender.Hex(), msg.Nonce, stateNonce)
		}
		if msg.Nonce > stateNonce {
			return applyCallResult{}, fmt.Errorf("%w: address %v, tx: %d state: %d",
				core.ErrNonceTooHigh, sender.Hex(), msg.Nonce, stateNonce)
		}
		// London fee checks
		if sim.chainConfig.IsLondon(evm.Context.BlockNumber) {
			if msg.GasFeeCap.Cmp(evm.Context.BaseFee) < 0 {
				return applyCallResult{}, fmt.Errorf("%w: address %v, maxFeePerGas: %s, baseFee: %s",
					core.ErrFeeCapTooLow, sender.Hex(), msg.GasFeeCap, evm.Context.BaseFee)
			}
		}
	}
	// Balance check: gasLimit * gasFeeCap + value.
	// This mirrors geth's buyGas() which always runs regardless of validation
	// mode. Without this, insufficient-funds scenarios silently proceed as
	// VM-level failures instead of aborting the block.
	{
		balanceCheck := new(big.Int).SetUint64(msg.GasLimit)
		if msg.GasFeeCap != nil {
			balanceCheck.Mul(balanceCheck, msg.GasFeeCap)
		}
		if msg.Value != nil {
			balanceCheck.Add(balanceCheck, msg.Value)
		}
		balance := sim.state.GetBalance(sender)
		balanceU256, _ := uint256.FromBig(balanceCheck)
		if balance.Cmp(balanceU256) < 0 {
			return applyCallResult{}, fmt.Errorf("%w: address %v have %v want %v (supplied gas %d)",
				core.ErrInsufficientFunds, sender.Hex(), balance, balanceCheck, msg.GasLimit)
		}
	}
```

**File:** x/evm/keeper/simulate.go (L438-451)
```go
	if contractCreation {
		ret, _, leftoverGas, vmErr = evm.Create(msg.From, msg.Data, leftoverGas, value)
	} else {
		if msg.SetCodeAuthorizations != nil {
			for _, auth := range msg.SetCodeAuthorizations {
				if _, err := sim.keeper.applyAuthorization(&auth, sim.state); err != nil {
					sim.keeper.Logger(sim.state.Context()).Debug("simulation: failed to apply authorization",
						"error", err, "authorization", auth)
				}
			}
		}
		ret, leftoverGas, vmErr = evm.Call(msg.From, *msg.To, msg.Data, leftoverGas, value)
		sim.state.SetNonce(msg.From, msg.Nonce+1, tracing.NonceChangeUnspecified)
	}
```

**File:** rpc/types/simulate_errors.go (L38-58)
```go
	ErrCodeSenderIsNotEOA          = -38024
	ErrCodeMaxInitCodeSizeExceeded = -38025
	ErrCodeClientLimitExceeded     = -38026
	ErrCodeInternalError           = -32603
	ErrCodeInvalidParams           = -32602
	ErrCodeVMError                 = -32015
	ErrCodeServerError             = -32000
)

// TxValidationError maps core transaction validation errors to JSON-RPC error codes.
func TxValidationError(err error) *InvalidTxError {
	if err == nil {
		return nil
	}
	switch {
	case errors.Is(err, core.ErrNonceTooHigh):
		return &InvalidTxError{Message: err.Error(), Code: ErrCodeNonceTooHigh}
	case errors.Is(err, core.ErrNonceTooLow):
		return &InvalidTxError{Message: err.Error(), Code: ErrCodeNonceTooLow}
	case errors.Is(err, core.ErrSenderNoEOA):
		return &InvalidTxError{Message: err.Error(), Code: ErrCodeSenderIsNotEOA}
```
