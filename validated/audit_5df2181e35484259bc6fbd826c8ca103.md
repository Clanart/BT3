### Title
Self-destructing a contract whose Cosmos account holds locked (vesting) balance permanently burns the locked funds instead of transferring or unlocking them - ([File: x/vm/keeper/statedb.go])

### Summary
The reported MetaVesT bug (`getVestedTokenAmount` locking funds when termination precedes vesting start) maps to the same invariant-class in Cosmos EVM: **an account-state transition that should preserve or redirect a user's balance instead silently destroys it with no path to recovery.** In `x/vm/keeper/statedb.go`, `DeleteAccount` (invoked from `EVM SELFDESTRUCT`/`StateDB.Commit`) converts the target Cosmos account to a plain `BaseAccount` and then zeroes its **total** balance via `SetBalance`, which burns the full delta from the bank module. However, the EVM-side `GetBalance`/`AddBalance` semantics used by the go-ethereum interpreter to compute the SELFDESTRUCT beneficiary transfer are based only on `SpendableCoin` (i.e., the *unlocked* portion) at `GetAccount` time [1](#0-0) . Any *locked* (vesting) balance on that same address is never sent to the beneficiary, yet it is unconditionally burned at commit time.

### Finding Description
`DeleteAccount` implements Ethereum `SELFDESTRUCT` cleanup: [2](#0-1) 

Steps:
1. It replaces the account with `authtypes.NewBaseAccount(...)`, explicitly to "set the whole balance as spendable" — i.e., it forcibly strips any vesting/lock schedule.
2. It then calls `k.SetBalance(ctx, addr, new(uint256.Int))`, i.e., target balance = 0.

`SetBalance` computes `delta = target - currentSpendable` and burns the difference from the bank module: [3](#0-2) 

Because step 1 already converted the account to a `BaseAccount`, `SpendableCoin` at the time of `SetBalance` reflects the **entire** total balance (previously-locked + previously-spendable), so the full amount is burned unconditionally — there is no code path that forwards any portion of it to the EVM SELFDESTRUCT beneficiary or any other recoverable destination.

Meanwhile, during EVM execution (before `Commit`/`DeleteAccount` runs), the go-ethereum interpreter's `SELFDESTRUCT` opcode determines the beneficiary-transfer amount using `StateDB.GetBalance`, which is populated from `k.GetAccount`: [1](#0-0) 
`k.SpendableCoin` only returns the *unlocked* portion of a restricted/vesting account. Consequently, if a contract's underlying Cosmos account carries a locked balance (vesting, streaming, or otherwise restricted coins), the interpreter's `AddBalance(beneficiary, ...)` transfer only moves the spendable portion. The locked remainder is invisible to the EVM balance accounting throughout the transaction, and is destroyed by `DeleteAccount`'s unconditional full-balance burn when the transaction commits.

This exactly parallels the reported bug's class: a boundary/edge condition (funds under a lock/vesting schedule) is not accounted for by the code path that finalizes the destructive state transition (contract termination / self-destruct), resulting in irreversible loss of user value rather than either forwarding it to a valid recipient or reverting the operation.

The unit test in the repository actually documents and locks in this exact behavior as "expected": [4](#0-3) 
It asserts that after making a contract's Cosmos account a `ContinuousVestingAccount` with a non-zero total balance (`SpendableCoin == 0`, `EVM balance == 0`, `total bank balance == 100`), calling `DeleteAccount` succeeds and the test does not verify any beneficiary received the 100 — it only checks the source account balance becomes zero. The 100 units of native denom vanish from total supply expectations without being credited anywhere observable in this call path.

### Impact Explanation
If a contract address's underlying Cosmos account can hold vested/locked native coins (e.g. because a vesting schedule was applied to that address, or code was deployed to an address that already had a vesting account), a `SELFDESTRUCT` from that contract permanently and irreversibly burns the locked balance instead of transferring it to the beneficiary or any recoverable account. This is a critical, irreversible accounting corruption / permanent loss of user funds: total effective circulating value is destroyed with no governance or user recovery mechanism, directly matching the "permanent freezing, locking, theft, or unauthorized extraction of user funds" and "irreversible accounting corruption of spendable user value across native balances" impact categories.

### Likelihood Explanation
The trigger (`SELFDESTRUCT`) is fully within reach of an unprivileged EVM user controlling a contract. The remaining question — and the part I could not fully verify within this investigation — is the exact mechanism by which a contract's associated Cosmos account acquires a non-zero locked/vesting balance in production (e.g., via `x/vesting`, an airdrop/vesting precompile, or a genesis vesting grant to an address that is later used for `CREATE`/`CREATE2` contract deployment). The test suite exercises this scenario directly by manually assigning a `ContinuousVestingAccount` to a contract address and confirming `DeleteAccount` burns the full balance, so the vulnerable code path is real and reachable; determining the specific unprivileged on-chain vector to get a vesting account onto a contract address requires further investigation of `x/vm`'s account-creation guards and any vesting-related precompiles/modules exposed to users.

### Recommendation
- In `DeleteAccount`, before burning, explicitly account for the case where the account being deleted is not a plain `BaseAccount` (i.e., holds locked/vesting balance). Either (a) reject self-destruct when locked balance exists on the account and require it to be handled first, or (b) transfer the total (not spendable-only) balance amount to the SELFDESTRUCT beneficiary rather than the truncated "spendable-only" figure surfaced through `GetAccount`/`SpendableCoin`.
- Ensure the EVM `GetBalance`/`AddBalance` semantics used within a single transaction are consistent with what `DeleteAccount` ultimately destroys, so no portion of an address's total native balance can be dropped between the interpreter's transfer accounting and the keeper's final cleanup.
- Add invariant checks/tests asserting `sum(balances before) == sum(balances after)` (module + address) across a SELFDESTRUCT of an account with mixed locked/unlocked balance, to catch any accounting mismatch instead of asserting only that the source's balance became zero.

### Proof of Concept
1. Deploy a contract to an address `A` (via normal `CREATE`).
2. Cause the associated Cosmos account for `A` to become a `ContinuousVestingAccount` holding, e.g., 100 units of the EVM denom that are fully locked (as exactly reproduced in `tests/integration/x/vm/test_statedb.go` `TestDeleteAccount`, case "removing vested account should remove all balance (including locked)") [5](#0-4) .
3. From within a transaction, execute `SELFDESTRUCT` on contract `A` targeting an arbitrary beneficiary address `B`.
4. Observe: the interpreter's beneficiary transfer to `B` is based on `GetBalance(A)` which returns `0` (since `SpendableCoin` is `0` due to vesting lock), so `B` receives nothing.
5. On `StateDB.Commit()`, `x/vm` keeper's `DeleteAccount` converts `A` to a `BaseAccount` and burns its full underlying bank balance (`100`), as confirmed by the existing test asserting `GetBalance(A) == 0` and no account remains — the `100` units are burned rather than reaching `B` or any recoverable holder.

### Citations

**File:** x/vm/keeper/statedb.go (L27-35)
```go
func (k *Keeper) GetAccount(ctx sdk.Context, addr common.Address) *statedb.Account {
	acct := k.GetAccountWithoutBalance(ctx, addr)
	if acct == nil {
		return nil
	}

	acct.Balance = k.SpendableCoin(ctx, addr)
	return acct
}
```

**File:** x/vm/keeper/statedb.go (L111-136)
```go
// SetBalance update account's balance, compare with current balance first, then decide to mint or burn.
func (k *Keeper) SetBalance(ctx sdk.Context, addr common.Address, amount *uint256.Int) error {
	if amount == nil {
		return nil
	}
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	coin := k.bankWrapper.SpendableCoin(ctx, cosmosAddr, types.GetEVMCoinDenom())

	balance := coin.Amount.BigInt()
	delta := new(big.Int).Sub(amount.ToBig(), balance)
	switch delta.Sign() {
	case 1:
		// mint
		if err := k.bankWrapper.MintAmountToAccount(ctx, cosmosAddr, delta); err != nil {
			return err
		}
	case -1:
		// burn
		if err := k.bankWrapper.BurnAmountFromAccount(ctx, cosmosAddr, new(big.Int).Neg(delta)); err != nil {
			return err
		}
	default:
		// not changed
	}
	return nil
}
```

**File:** x/vm/keeper/statedb.go (L243-268)
```go
// DeleteAccount handles contract's suicide call:
// - clear balance
// - remove code
// - remove states
// - remove the code hash
// - remove auth account
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum contracts can be self-destructed
	if !k.IsContract(ctx, addr) {
		return errors.New("only smart contracts can be self-destructed")
	}

	// set account to a base account to set the whole balance as spendable
	baseAccount := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	k.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(cosmosAddr, baseAccount.GetPubKey(), baseAccount.GetAccountNumber(), baseAccount.GetSequence()))

	// clear balance
	if err := k.SetBalance(ctx, addr, new(uint256.Int)); err != nil {
		return err
	}
```

**File:** tests/integration/x/vm/test_statedb.go (L1081-1115)
```go
		{
			name: "removing vested account should remove all balance (including locked)",
			malleate: func() common.Address {
				contractAccAddr := sdk.AccAddress(contractAddr.Bytes())
				err := s.Network.App.GetBankKeeper().SendCoins(ctx, s.Keyring.GetAccAddr(0), contractAccAddr, sdk.NewCoins(sdk.NewCoin(s.Network.GetBaseDenom(), math.NewInt(100))))
				s.Require().NoError(err)
				// replace with vesting account
				balanceResp, err := s.Handler.GetBalanceFromEVM(contractAccAddr)
				s.Require().NoError(err)

				balance, ok := math.NewIntFromString(balanceResp.Balance)
				s.Require().True(ok)

				ctx := s.Network.GetContext()
				baseAccount := s.Network.App.GetAccountKeeper().GetAccount(ctx, contractAccAddr).(*authtypes.BaseAccount)
				baseDenom := s.Network.GetBaseDenom()
				currTime := s.Network.GetContext().BlockTime().Unix()
				acc, err := vestingtypes.NewContinuousVestingAccount(baseAccount, sdk.NewCoins(sdk.NewCoin(baseDenom, balance)), s.Network.GetContext().BlockTime().Unix(), currTime+100)
				s.Require().NoError(err)
				s.Network.App.GetAccountKeeper().SetAccount(ctx, acc)

				spendable := s.Network.App.GetBankKeeper().SpendableCoin(ctx, contractAccAddr, baseDenom).Amount
				s.Require().Equal(spendable.String(), "0")

				evmBalanceRes, err := s.Handler.GetBalanceFromEVM(contractAccAddr)
				s.Require().NoError(err)
				evmBalance := evmBalanceRes.Balance
				s.Require().Equal(evmBalance, "0")

				totalBalance := s.Network.App.GetBankKeeper().GetBalance(ctx, contractAccAddr, baseDenom)
				s.Require().Equal(totalBalance.Amount, balance)
				return contractAddr
			},
			expPass: true,
		},
```
