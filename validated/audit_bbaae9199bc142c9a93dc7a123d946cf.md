### Title
Gas Fees Permanently Lost When `ApplyMessageWithConfig` Returns Cosmos-Level Error for Oversized Init Code — (File: `x/evm/keeper/state_transition.go`)

---

### Summary

When a contract-creation transaction whose `len(data) > MaxInitCodeSize` (49 152 bytes, post-Shanghai) is submitted, it passes the ante handler — which never checks init-code size — has its full `gasLimit × effectiveGasPrice` fee deducted, and then fails inside `ApplyMessageWithConfig` with a **cosmos-level error** (not a vmErr). Because `RefundGas` is only reached when `ApplyMessageWithConfig` returns without error, the pre-deducted fee is permanently lost to the user.

---

### Finding Description

**Step 1 — Ante handler deducts full fees without checking init-code size.**

`CheckEthGasConsume` calls `VerifyFee`, which computes and deducts `gasLimit × effectiveGasPrice`. `VerifyFee` checks intrinsic gas (CheckTx only), EIP-7623 floor data gas, and baseFee — but **never** checks `len(data) > MaxInitCodeSize`. [1](#0-0) [2](#0-1) 

**Step 2 — `ApplyMessageWithConfig` returns a cosmos-level error for oversized init code.**

After the ante handler commits the fee deduction, `ApplyMessageWithConfig` performs the init-code size check and returns `nil, fmt.Errorf(...)` — a cosmos-level error, not a `VmError` field in the result: [3](#0-2) 

**Step 3 — `ApplyTransaction` exits early; `RefundGas` is never reached.**

`ApplyTransaction` propagates the cosmos-level error immediately, bypassing the `RefundGas` call that would have returned unused gas to the sender: [4](#0-3) [5](#0-4) 

`RefundGas` sends leftover gas back from the fee-collector module to the sender. When it is skipped, the entire pre-deducted fee stays in the fee collector and is never returned. [6](#0-5) 

**Contrast with the vmErr path.** When the EVM itself rejects a transaction (e.g., `out of gas`, `revert`), `ApplyMessageWithConfig` returns `(result, nil)` with `result.VmError` set. `ApplyTransaction` then reaches `RefundGas` and correctly refunds unused gas. The cosmos-level-error path has no equivalent refund. [7](#0-6) 

**Secondary paths with the same root cause.** The same `RefundGas`-skip affects any other cosmos-level error from `ApplyMessageWithConfig`:

- `EnableCreate` / `EnableCall` disabled by governance between CheckTx and DeliverTx (lines 352–356).
- `durableStateDB.Commit()` failure for EIP-7702 authorizations (lines 510–512).
- Non-`ErrStateConflict` `stateDB.Commit()` failure (line 622). [8](#0-7) [9](#0-8) [10](#0-9) 

---

### Impact Explanation

Any user who submits a contract-creation transaction with `len(data) > 49 152` bytes loses the entire pre-deducted fee (`gasLimit × effectiveGasPrice`). Unlike a normal EVM revert — where unused gas is refunded — the cosmos-level error path provides zero refund. A user setting a generous gas limit (e.g., 10 M gas at 10 Gwei) loses the full amount. A validator has a direct financial incentive to include such transactions because the fee collector retains the funds.

This matches the allowed High impact: *"EVM state transition, gas refund, fee market, ante handler, or proposal handling bug that permits valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

The path is fully unprivileged:

1. Any user can craft a contract-creation transaction with `len(data) > 49 152` bytes.
2. The ante handler does not check init-code size, so the transaction passes CheckTx and enters the mempool.
3. A validator includes it in a block; during DeliverTx the ante handler again passes, fees are deducted, and `ApplyMessageWithConfig` returns a cosmos-level error.
4. `RefundGas` is never called; the user's funds are permanently lost.

No privileged role, governance action, or validator collusion is required for the user to lose funds — the user can trigger this accidentally by deploying a large contract.

---

### Recommendation

Two complementary fixes:

1. **Add init-code size validation to the ante handler** (in `VerifyFee` or `CheckEthGasConsume`) so the transaction is rejected before any fee is deducted, consistent with how EIP-7623 floor data gas is enforced unconditionally.

2. **Convert the init-code size check in `ApplyMessageWithConfig` to a vmErr** (set `result.VmError` and return `(result, nil)`) so that `RefundGas` is always reached for user-submitted transactions, matching go-ethereum's behavior where `evm.Create` surfaces this as an EVM-level failure. [11](#0-10) [3](#0-2) 

---

### Proof of Concept

```
1. Attacker/user constructs a MsgEthereumTx:
     To:       nil  (contract creation)
     Data:     50 000 bytes of arbitrary bytecode  (> MaxInitCodeSize = 49 152)
     GasLimit: 5_000_000
     GasPrice: 10 Gwei

2. CheckTx:
     VerifyFee → intrinsic gas check skipped (DeliverTx path) / EIP-7623 check passes
     (init-code size NOT checked)
     → tx admitted to mempool

3. DeliverTx ante handler:
     VerifyFee computes fee = 5_000_000 × 10 Gwei = 50_000 Gwei
     DeductTxCostsFromUserBalance deducts 50_000 Gwei from sender → fee collector
     (init-code size still NOT checked)

4. EthereumTx → ApplyTransaction → ApplyMessageWithConfig:
     line 459: len(msg.Data)=50_000 > MaxInitCodeSize=49_152 → TRUE
     line 461: return nil, fmt.Errorf("max initcode size exceeded …")   ← cosmos-level error

5. ApplyTransaction line 195-197:
     err != nil → return nil, error   ← RefundGas at line 252 is NEVER reached

6. Result:
     User's 50_000 Gwei remain in fee collector.
     No refund. Funds permanently lost.
```

### Citations

**File:** ante/eth.go (L170-178)
```go
		fees, err := keeper.VerifyFee(msgEthTx, evmDenom, baseFee, rules, ctx.IsCheckTx())
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to verify the fees")
		}

		err = evmKeeper.DeductTxCostsFromUserBalance(ctx, fees, common.BytesToAddress(msgEthTx.From))
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to deduct transaction costs from user balance")
		}
```

**File:** x/evm/keeper/utils.go (L118-164)
```go
func VerifyFee(
	msg *types.MsgEthereumTx,
	denom string,
	baseFee *big.Int,
	rules params.Rules, isCheckTx bool,
) (sdk.Coins, error) {
	tx := msg.AsTransaction()
	isContractCreation := tx.To() == nil

	gasLimit := tx.Gas()

	accessList := tx.AccessList()
	intrinsicGas, err := core.IntrinsicGas(
		tx.Data(),
		accessList,
		tx.SetCodeAuthorizations(),
		isContractCreation,
		rules.IsHomestead,
		rules.IsIstanbul,
		rules.IsShanghai,
	)
	if err != nil {
		return nil, errorsmod.Wrapf(
			err,
			"failed to retrieve intrinsic gas, contract creation = %t; homestead = %t, istanbul = %t, shanghai = %t",
			isContractCreation, rules.IsHomestead, rules.IsIstanbul, rules.IsShanghai,
		)
	}

	// intrinsic gas verification during CheckTx
	if isCheckTx && gasLimit < intrinsicGas {
		return nil, errorsmod.Wrapf(
			errortypes.ErrOutOfGas,
			"gas limit too low: %d (gas limit) < %d (intrinsic gas)", gasLimit, intrinsicGas,
		)
	}

	// Gas limit suffices for the floor data cost (EIP-7623)
	if rules.IsPrague {
		floorDataGas, err := core.FloorDataGas(tx.Data())
		if err != nil {
			return nil, err
		}
		if gasLimit < floorDataGas {
			return nil, errorsmod.Wrapf(core.ErrFloorDataGas, "gas %v, minimum needed %v", tx.Gas(), floorDataGas)
		}
	}
```

**File:** x/evm/keeper/state_transition.go (L194-197)
```go
	res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
	}
```

**File:** x/evm/keeper/state_transition.go (L249-254)
```go
	leftoverGas := msg.GasLimit - res.GasUsed

	// refund gas in order to match the Ethereum gas consumption instead of the default SDK one.
	if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
		return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
	}
```

**File:** x/evm/keeper/state_transition.go (L352-356)
```go
	if !cfg.Params.EnableCreate && msg.To == nil {
		return nil, errorsmod.Wrap(types.ErrCreateDisabled, "failed to create new contract")
	} else if !cfg.Params.EnableCall && msg.To != nil {
		return nil, errorsmod.Wrap(types.ErrCallDisabled, "failed to call contract")
	}
```

**File:** x/evm/keeper/state_transition.go (L459-461)
```go
	if rules.IsShanghai && contractCreation && len(msg.Data) > params.MaxInitCodeSize {
		return nil, fmt.Errorf("%w: code size %v limit %v", core.ErrMaxInitCodeSizeExceeded, len(msg.Data), params.MaxInitCodeSize)
	}
```

**File:** x/evm/keeper/state_transition.go (L510-512)
```go
				if err := durableStateDB.Commit(); err != nil {
					return nil, errorsmod.Wrap(err, "failed to commit durable EIP-7702 authorization stateDB")
				}
```

**File:** x/evm/keeper/state_transition.go (L558-563)
```go
	// EVM execution error needs to be available for the JSON-RPC client
	var vmError string
	if vmErr != nil {
		vmError = vmErr.Error()
	}

```

**File:** x/evm/keeper/state_transition.go (L620-623)
```go
			}

			return nil, errorsmod.Wrap(err, "failed to commit stateDB")
		}
```

**File:** x/evm/keeper/gas.go (L50-88)
```go
func (k *Keeper) RefundGas(ctx sdk.Context, msg *core.Message, leftoverGas uint64, denom string) error {
	return k.RefundGasWithPrice(ctx, msg, leftoverGas, msg.GasPrice, denom)
}

// RefundGasWithPrice transfers the leftover gas to sender using the provided gas price.
func (k *Keeper) RefundGasWithPrice(
	ctx sdk.Context,
	msg *core.Message,
	leftoverGas uint64,
	gasPrice *big.Int,
	denom string,
) error {
	if gasPrice == nil {
		gasPrice = new(big.Int)
	}

	// Return EVM tokens for remaining gas, exchanged at the original rate.
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(leftoverGas), gasPrice)

	switch remaining.Sign() {
	case -1:
		// negative refund errors
		return errorsmod.Wrapf(types.ErrInvalidRefund, "refunded amount value cannot be negative %d", remaining.Int64())
	case 1:
		// positive amount refund
		refundedCoins := sdk.Coins{sdk.NewCoin(denom, sdkmath.NewIntFromBigInt(remaining))}

		// refund to sender from the fee collector module account, which is the escrow account in charge of collecting tx fees
		err := k.bankKeeper.SendCoinsFromModuleToAccountVirtual(ctx, authtypes.FeeCollectorName, msg.From.Bytes(), refundedCoins)
		if err != nil {
			err = errorsmod.Wrapf(errortypes.ErrInsufficientFunds, "fee collector account failed to refund fees: %s", err.Error())
			return errorsmod.Wrapf(err, "failed to refund %d leftover gas (%s)", leftoverGas, refundedCoins.String())
		}
	default:
		// no refund, consume gas and update the tx gas meter
	}

	return nil
}
```
