Confirmed: `core.CanTransfer`/`core.Transfer` (standard geth semantics, per `x/vm/keeper/state_transition.go:46-47`) move `msg.value` in the StateDB at the EVM-call level before `Run()` of any target (including precompiles) is ever invoked. This means when a caller sends `value > 0` to the WERC20 precompile address, the balance debit/credit already happened in `statedb.StateDB` regardless of which method is later dispatched.

### Title
WERC20 precompile allows attaching native value to non-deposit methods, permanently trapping funds in the EVM StateDB without a corresponding bank-module movement - (File: precompiles/werc20/werc20.go)

### Summary
The base ERC20 precompile's `Execute()` explicitly rejects any call carrying `msg.value` (`contract.Value().Sign() == 1`) precisely because — as the code comment states — "funds cannot be received due to the lack of a fallback handler" and would be irrecoverably stuck. The WERC20 precompile is a thin wrapper that overrides `Execute()` entirely and only special-cases `deposit`/fallback/`receive` (which properly return the coins via `BankKeeper.SendCoins`) and `withdraw` (a no-op). Every other ABI method (`transfer`, `transferFrom`, `approve`, `balanceOf`, etc.) falls through to `p.HandleMethod(...)`, which is inherited unchanged from the ERC20 precompile and never re-applies the "cannot receive funds" guard.

### Finding Description [1](#0-0) 
The base precompile's safety check only fires inside its own `Execute()`. [2](#0-1)  shows WERC20's `Execute()` is a full override that dispatches non-deposit/withdraw calls straight to `p.HandleMethod`, bypassing the value check. Because the EVM's `core.CanTransfer`/`core.Transfer` block-context callbacks perform the `msg.value` StateDB balance transfer generically for every call target before the precompile's `Run`/`Execute` is invoked ( [3](#0-2) ), a transaction such as `WERC20.transfer{value: X}(to, amount)` will already have moved `X` aatom from the caller's StateDB balance to the WERC20 precompile address's StateDB balance by the time `HandleMethod`/`Transfer` executes. `Transfer`/`TransferFrom`/`Approve` ( [4](#0-3)  ) only perform a `bank.MsgSend` for the ERC20-mapped denom argument; they never touch `contract.Value()` or issue any `BankKeeper.SendCoins` to refund the attached native value, unlike `Deposit()` ( [5](#0-4) ) which explicitly sends the deposited coins back to the caller.

The `BalanceHandler.AfterBalanceChange` mechanism ( [6](#0-5) ) only reconciles StateDB balances based on `x/bank` `EventTypeCoinSpent`/`EventTypeCoinReceived` events that were actually emitted during the precompile call — since no bank-side coin movement occurs for the attached `msg.value` in this path, there is nothing to reconcile, and the raw EVM-level value transfer into the precompile address's StateDB balance stands uncorrected. The WERC20 precompile has no fallback/withdraw mechanism that can recover this balance (as `Withdraw` is a no-op that never moves funds, per [7](#0-6) ), so the value becomes permanently unspendable — the same "lack of a fallback handler" failure the base ERC20 precompile explicitly guards against.

### Impact Explanation
This causes irreversible loss/locking of a caller's native token balance: an unprivileged user calling any WERC20 ABI method other than `deposit`/`withdraw`/fallback/receive while attaching `msg.value` permanently loses that value into a precompile address that has no code and no mechanism to move funds back out. This matches the "Critical permanent freezing/locking/theft of user funds" impact class, mirroring exactly the analog bug class from the external report (native-token handling inconsistency between an outer safety check and an inner execution path that doesn't uphold the same invariant).

### Likelihood Explanation
Any unprivileged EOA or contract can trigger this by directly calling the WERC20 precompile with a non-deposit selector and non-zero `msg.value` (e.g., an accidental `approve{value: X}(...)` from a wallet/dApp that doesn't realize WERC20 is a precompile without special value handling for those methods, or a malicious contract tricking a user into such a call). No special privileges, races, or governance actions are required — this is a straightforward, deterministic per-transaction bug reachable through ordinary contract interaction.

### Recommendation
Reinstate the value-rejection guard for all non-deposit/receive/fallback methods in `werc20.Execute()` (i.e., call the equivalent of `erc20.Precompile.Execute`'s check, or explicitly check `contract.Value().Sign() == 1` before falling through to `HandleMethod` in the `default` case), so only `Deposit` (which properly returns funds via `BankKeeper.SendCoins`) is allowed to carry `msg.value`.

### Proof of Concept
1. Deploy/target the WERC20 precompile address for the chain's native EVM denom.
2. From an EOA with balance, submit a call to the precompile with `data = abi.encode(approve(spender, amount))` (or `transfer`/`transferFrom`) and `value = X aatom` (X > 0).
3. Observe: the EVM call mechanism (`core.Transfer`) debits `X` from the caller's StateDB balance and credits it to the WERC20 precompile address's StateDB balance before `Execute()` runs.
4. `Execute()` dispatches to the `default` branch → `HandleMethod` → `Approve`/`Transfer`, none of which reference `contract.Value()` or refund it.
5. Transaction succeeds; the precompile "holds" `X` aatom in EVM state with no code, no owner key, and no `withdraw` logic capable of extracting it — the funds are permanently unspendable, while the corresponding `x/bank`/`x/precisebank` balances were never touched, creating a StateDB-vs-bank divergence for that value.

### Citations

**File:** precompiles/erc20/erc20.go (L148-163)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	// ERC20 precompiles cannot receive funds because they are not managed by an
	// EOA and will not be possible to recover funds sent to an instance of
	// them.This check is a safety measure because currently funds cannot be
	// received due to the lack of a fallback handler.
	if value := contract.Value(); value.Sign() == 1 {
		return nil, fmt.Errorf(ErrCannotReceiveFunds, contract.Value().String())
	}

	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	return p.HandleMethod(ctx, contract, stateDB, method, args)
}
```

**File:** precompiles/werc20/werc20.go (L103-124)
```go
func (p Precompile) Execute(ctx sdk.Context, stateDB vm.StateDB, contract *vm.Contract, readOnly bool) ([]byte, error) {
	method, args, err := cmn.SetupABI(p.ABI, contract, readOnly, p.IsTransaction)
	if err != nil {
		return nil, err
	}

	var bz []byte

	switch {
	case method.Type == abi.Fallback,
		method.Type == abi.Receive,
		method.Name == DepositMethod:
		bz, err = p.Deposit(ctx, contract, stateDB)
	case method.Name == WithdrawMethod:
		bz, err = p.Withdraw(ctx, contract, stateDB, args)
	default:
		// ERC20 transactions and queries
		bz, err = p.HandleMethod(ctx, contract, stateDB, method, args)
	}

	return bz, err
}
```

**File:** x/vm/keeper/state_transition.go (L44-56)
```go
	ctx = k.SetConsensusParamsInCtx(ctx)
	blockCtx := vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    core.Transfer,
		GetHash:     k.GetHashFn(ctx),
		Coinbase:    cfg.CoinBase,
		GasLimit:    antetypes.BlockGasLimit(ctx),
		BlockNumber: big.NewInt(ctx.BlockHeight()),
		Time:        uint64(ctx.BlockHeader().Time.Unix()), //#nosec G115 -- int overflow is not a concern here
		Difficulty:  big.NewInt(0),                         // unused. Only required in PoW context
		BaseFee:     cfg.BaseFee,
		Random:      &common.MaxHash, // need to be different than nil to signal it is after the merge and pick up the right opcodes
	}
```

**File:** precompiles/erc20/tx.go (L69-120)
```go
func (p *Precompile) transfer(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
	method *abi.Method,
	from, to common.Address,
	amount *big.Int,
) (data []byte, err error) {
	coins := sdk.Coins{{Denom: p.tokenPair.Denom, Amount: math.NewIntFromBigInt(amount)}}

	msg := banktypes.NewMsgSend(from.Bytes(), to.Bytes(), coins)

	if err = msg.Amount.Validate(); err != nil {
		return nil, err
	}

	isTransferFrom := method.Name == TransferFromMethod
	spenderAddr := contract.Caller()
	newAllowance := big.NewInt(0)

	if isTransferFrom {
		prevAllowance, err := p.erc20Keeper.GetAllowance(ctx, p.Address(), from, spenderAddr)
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}

		newAllowance = new(big.Int).Sub(prevAllowance, amount)
		if newAllowance.Sign() < 0 {
			return nil, ErrInsufficientAllowance
		}

		if newAllowance.Sign() == 0 {
			// If the new allowance is 0, we need to delete it from the store.
			err = p.erc20Keeper.DeleteAllowance(ctx, p.Address(), from, spenderAddr)
		} else {
			// If the new allowance is not 0, we need to set it in the store.
			err = p.erc20Keeper.SetAllowance(ctx, p.Address(), from, spenderAddr, newAllowance)
		}
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}
	}

	msgSrv := NewMsgServerImpl(p.BankKeeper)
	if err = msgSrv.Send(ctx, msg); err != nil {
		// This should return an error to avoid the contract from being executed and an event being emitted
		return nil, ConvertErrToERC20Error(err)
	}

	if err = p.EmitTransferEvent(ctx, stateDB, from, to, amount); err != nil {
		return nil, err
	}
```

**File:** precompiles/werc20/tx.go (L28-57)
```go
func (p Precompile) Deposit(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
) ([]byte, error) {
	caller := contract.Caller()
	depositedAmount := contract.Value()

	callerAccAddress := sdk.AccAddress(caller.Bytes())
	precompileAccAddr := sdk.AccAddress(p.Address().Bytes())

	// Send the coins back to the sender
	if err := p.BankKeeper.SendCoins(
		ctx,
		precompileAccAddr,
		callerAccAddress,
		sdk.NewCoins(sdk.Coin{
			Denom:  evmtypes.GetEVMCoinExtendedDenom(),
			Amount: math.NewIntFromBigInt(depositedAmount.ToBig()),
		}),
	); err != nil {
		return nil, err
	}

	if err := p.EmitDepositEvent(ctx, stateDB, caller, depositedAmount.ToBig()); err != nil {
		return nil, err
	}

	return nil, nil
}
```

**File:** precompiles/werc20/tx.go (L59-79)
```go
// Withdraw is a no-op and mock function that provides the same interface as the
// WETH contract to support equality between the native coin and its wrapped
// ERC-20 (e.g. ATOM and WEVMOS).
func (p Precompile) Withdraw(ctx sdk.Context, contract *vm.Contract, stateDB vm.StateDB, args []interface{}) ([]byte, error) {
	amount, ok := args[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("invalid argument type: %T", args[0])
	}
	amountInt := math.NewIntFromBigInt(amount)

	caller := contract.Caller()
	callerAccAddress := sdk.AccAddress(caller.Bytes())
	nativeBalance := p.BankKeeper.SpendableCoin(ctx, callerAccAddress, evmtypes.GetEVMCoinDenom())
	if nativeBalance.Amount.Mul(types.ConversionFactor()).LT(amountInt) {
		return nil, fmt.Errorf("account balance %v is lower than withdraw balance %v", nativeBalance.Amount, amountInt)
	}

	if err := p.EmitWithdrawalEvent(ctx, stateDB, caller, amount); err != nil {
		return nil, err
	}
	return nil, nil
```

**File:** precompiles/common/balance_handler.go (L68-139)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
		switch event.Type {
		case banktypes.EventTypeCoinSpent:
			spenderAddr, err := ParseAddress(event, banktypes.AttributeKeySpender)
			if err != nil {
				return fmt.Errorf("failed to parse spender address from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}
			if bh.bankKeeper.BlockedAddr(spenderAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinSpent, err)
			}

			stateDB.SubBalance(common.BytesToAddress(spenderAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)

		case banktypes.EventTypeCoinReceived:
			receiverAddr, err := ParseAddress(event, banktypes.AttributeKeyReceiver)
			if err != nil {
				return fmt.Errorf("failed to parse receiver address from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}
			if bh.bankKeeper.BlockedAddr(receiverAddr) {
				// Bypass blocked addresses
				continue
			}

			amount, err := ParseAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", banktypes.EventTypeCoinReceived, err)
			}

			stateDB.AddBalance(common.BytesToAddress(receiverAddr.Bytes()), amount, tracing.BalanceChangeUnspecified)

		case precisebanktypes.EventTypeFractionalBalanceChange:
			addr, err := ParseAddress(event, precisebanktypes.AttributeKeyAddress)
			if err != nil {
				return fmt.Errorf("failed to parse address from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}
			if bh.bankKeeper.BlockedAddr(addr) {
				// Bypass blocked addresses
				continue
			}

			delta, err := ParseFractionalAmount(event)
			if err != nil {
				return fmt.Errorf("failed to parse amount from event %q: %w", precisebanktypes.EventTypeFractionalBalanceChange, err)
			}

			deltaAbs, err := utils.Uint256FromBigInt(new(big.Int).Abs(delta))
			if err != nil {
				return fmt.Errorf("failed to convert delta to Uint256: %w", err)
			}

			if delta.Sign() == 1 {
				stateDB.AddBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			} else if delta.Sign() == -1 {
				stateDB.SubBalance(common.BytesToAddress(addr.Bytes()), deltaAbs, tracing.BalanceChangeUnspecified)
			}

		default:
			continue
		}
	}

	return nil
}
```
