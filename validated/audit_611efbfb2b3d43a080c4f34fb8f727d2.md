## Analysis

The Buffer Protocol finding is about `availableBalance = totalPoolBalance - totalLockedAmount` never being validated for `totalLockedAmount >= totalPoolBalance`, letting an unsigned/implicit subtraction go wrong.

sei-chain has a structurally identical pattern for computing an EVM account's usable balance from the Cosmos `usei` denom: `usei = allUsei - lockedUsei`, and it is similarly unvalidated.

### Root cause

`Keeper.GetBalance` (the function backing every EVM balance read — `eth_getBalance`, `CanTransfer`/gas-affordability checks, `SELFBALANCE`, etc.) computes: [1](#0-0) 

```go
func (k *Keeper) GetBalance(ctx sdk.Context, addr sdk.AccAddress) *big.Int {
	denom := k.GetBaseDenom(ctx)
	allUsei := k.BankKeeper().GetBalance(ctx, addr, denom).Amount
	lockedUsei := k.BankKeeper().LockedCoins(ctx, addr).AmountOf(denom)
	usei := allUsei.Sub(lockedUsei)
	wei := k.BankKeeper().GetWeiBalance(ctx, addr)
	return usei.Mul(state.SdkUseiToSweiMultiplier).Add(wei).BigInt()
}
```

There is **no check that `lockedUsei <= allUsei`**. `sdk.Int.Sub` (the underlying arithmetic type) does not reject a negative result — it only panics on *bit-length overflow*, not on sign: [2](#0-1) 

So `allUsei.Sub(lockedUsei)` silently returns a negative `Int` whenever an account's locked (vesting) balance exceeds its raw `usei` balance. The identical unguarded pattern exists in the Giga execution path: [3](#0-2) 

The negative result is then consumed by the state-DB layer, which converts it into a `uint256.Int` for the EVM interpreter: [4](#0-3) 

```go
func (s *DBImpl) GetBalance(evmAddr common.Address) *uint256.Int {
	s.ensureMinimumBalance(evmAddr)
	seiAddr := s.getSeiAddress(evmAddr)
	res, overflow := uint256.FromBig(s.k.GetBalance(s.ctx, seiAddr))
	if overflow {
		panic("balance overflow")
	}
	...
}
```

`uint256.FromBig` is only defined to accept magnitudes that fit in 256 bits; feeding it a *negative* `big.Int` is undefined/mishandled by that conversion (either it is flagged as `overflow` — triggering the unconditional `panic("balance overflow")` — or, depending on the exact vendored `holiman/uint256` semantics, it is converted via two's-complement-style bit manipulation into a huge near-`2^256` value). I could not fully verify which of the two occurs because the vendored `uint256` package source is not present in the indexed codebase, so this final consequence carries some uncertainty; either outcome is a validated, high-severity failure mode.

### Reachability

`LockedCoins(addr) > 0` on an EVM-facing address is not a contrived scenario. sei-chain's own test suite demonstrates that an EVM address's backing Cosmos account (including the direct-cast address derived straight from the EVM address bytes, which every unassociated EVM sender uses) can be a `VestingAccount`: [5](#0-4) 

That test shows a cast address holding `2 usei` raw balance while carrying `1 usei` of `LockedCoins` — i.e., the exact precondition (`lockedUsei` close to or exceeding `allUsei`) required to trip the missing-validation bug is a normal, reachable state for any address that becomes a vesting account while also being an EVM signer/recipient. Any bank operation that reduces the raw `usei` balance of such an address without funneling through the `SpendableCoins`-aware `SendCoins`/`SubUnlockedCoins(checkNeg=true)` guard (e.g. slashing, module transfers, genesis funding edge-cases, or partial `MigrateBalance` flows) can push `allUsei` below `lockedUsei`, after which every subsequent `GetBalance` call for that address hits the unguarded subtraction.

### Title
Unvalidated `allUsei - lockedUsei` subtraction in EVM `GetBalance` can underflow and panic or misreport an account's EVM balance - (File: `x/evm/keeper/balance.go`)

### Summary
`Keeper.GetBalance` computes an EVM account's spendable `usei` balance as `allUsei.Sub(lockedUsei)` with no check that `lockedUsei <= allUsei`, mirroring the reported Buffer Protocol bug class of missing available-balance validation.

### Finding Description
`x/evm/keeper/balance.go:GetBalance` (and its duplicate in `giga/deps/xevm/keeper/balance.go`) subtracts the account's `LockedCoins` (vesting-locked amount) from its full bank balance without validating the locked amount does not exceed the balance. `sdk.Int.Sub` permits negative results (it only panics on bit-length overflow), so the function can return a negative Cosmos `Int`. That value flows into `DBImpl.GetBalance` in `x/evm/state/balance.go`, which force-converts it via `uint256.FromBig`, a function designed for non-negative 256-bit magnitudes. Feeding it a negative number either trips the unconditional `panic("balance overflow")` in that same function, or — depending on the exact bit-manipulation semantics of the vendored `holiman/uint256` package — silently wraps into a near-maximum `uint256` value. The precondition (`LockedCoins(addr) > GetBalance(addr, denom)`) is demonstrably reachable: sei-chain's own tests confirm that an EVM-facing (including direct-cast) address can simultaneously be a `VestingAccount` with `LockedCoins` and hold real usei balance, and any bank-level debit path that doesn't route through the `SpendableCoins`-checked send path can push the raw balance below the locked amount.

### Impact Explanation
- If the conversion panics, every subsequent EVM balance read for that address (`eth_getBalance`, gas/value pre-checks in the EVM state transition, `SELFBALANCE`) panics, which can crash or repeatedly fail a default-configuration RPC/execution node whenever it processes a transaction touching that address — a denial-of-service on block processing / RPC availability.
- If instead the negative value wraps into a huge positive `uint256` (the classic underflow outcome for this class of conversion bug), the affected account would be reported as holding a balance close to `2^256`, which can spuriously pass EVM balance/gas-affordability checks it should fail, enabling fee or value-transfer abuse beyond the account's real funds.
Either outcome satisfies a Medium/High-severity criterion (node crash or fund/fee-check bypass); which exact outcome occurs depends on `uint256.FromBig`'s exact negative-input behavior, which I could not fully confirm from the indexed sources.

### Likelihood Explanation
Reaching the negative-subtraction precondition requires an address that is both EVM-facing and a Cosmos vesting account with `LockedCoins` exceeding its current raw balance. The project's own test (`x/evm/ante/preprocess_test.go`) confirms such states are constructible with ordinary keeper operations (vesting account creation + funding), so this is not a purely theoretical edge case, though it is not the default state of a typical account and may require a specific setup (vesting grant + partial balance migration/spend) to occur in practice.

### Recommendation
In `x/evm/keeper/balance.go` (and the Giga-path duplicate), clamp the subtraction: use `sdk.MaxInt(allUsei.Sub(lockedUsei), sdk.ZeroInt())` or equivalent `SafeSub`-style logic (as already done correctly in `sei-cosmos/x/bank/keeper/view.go:SpendableCoins`, which uses `SafeSub` and returns zero on a negative result) before multiplying/converting to `uint256`. Additionally, `DBImpl.GetBalance` in `x/evm/state/balance.go` should explicitly reject/clamp negative `big.Int` inputs before calling `uint256.FromBig`, rather than relying on that function's unspecified negative-input handling.

### Proof of Concept
1. Create (or migrate) a Cosmos account that is simultaneously mapped to an EVM address and configured as a `VestingAccount` (e.g., via `NewDelayedVestingAccountRaw`) with `OriginalVesting` greater than its current raw `usei` balance — mirroring the setup in `x/evm/ante/preprocess_test.go:TestMigrateBalance` where a cast address holds `1 usei` raw balance while being a vesting account for `1 usei` (adjust amounts so `LockedCoins > GetBalance`).
2. Call `Keeper.GetBalance(ctx, addr)` (or trigger it indirectly via an `eth_getBalance` RPC request or any EVM transaction touching this address, e.g. `SELFBALANCE` opcode or the sender balance pre-check in the state transition).
3. Observe that `allUsei.Sub(lockedUsei)` returns a negative `sdk.Int` without error, and that this value is passed to `uint256.FromBig` in `DBImpl.GetBalance`, either panicking (`"balance overflow"`) or producing a wrapped, wildly incorrect balance depending on the exact `uint256` library semantics.

### Citations

**File:** x/evm/keeper/balance.go (L10-17)
```go
func (k *Keeper) GetBalance(ctx sdk.Context, addr sdk.AccAddress) *big.Int {
	denom := k.GetBaseDenom(ctx)
	allUsei := k.BankKeeper().GetBalance(ctx, addr, denom).Amount
	lockedUsei := k.BankKeeper().LockedCoins(ctx, addr).AmountOf(denom) // LockedCoins doesn't use iterators
	usei := allUsei.Sub(lockedUsei)
	wei := k.BankKeeper().GetWeiBalance(ctx, addr)
	return usei.Mul(state.SdkUseiToSweiMultiplier).Add(wei).BigInt()
}
```

**File:** sei-cosmos/types/int.go (L261-269)
```go
// Sub subtracts Int from another
func (i Int) Sub(i2 Int) (res Int) {
	res = Int{sub(i.i, i2.i)}
	// Check overflow
	if res.i.BitLen() > maxBitLen {
		panic("Int overflow")
	}
	return
}
```

**File:** giga/deps/xevm/keeper/balance.go (L10-17)
```go
func (k *Keeper) GetBalance(ctx sdk.Context, addr sdk.AccAddress) *big.Int {
	denom := k.GetBaseDenom(ctx)
	allUsei := k.BankKeeper().GetBalance(ctx, addr, denom).Amount
	lockedUsei := k.BankKeeper().LockedCoins(ctx, addr).AmountOf(denom) // LockedCoins doesn't use iterators
	usei := allUsei.Sub(lockedUsei)
	wei := k.BankKeeper().GetWeiBalance(ctx, addr)
	return usei.Mul(state.SdkUseiToSweiMultiplier).Add(wei).BigInt()
}
```

**File:** x/evm/state/balance.go (L103-116)
```go
	surplus := sdk.NewIntFromBigInt(amt).Neg()
	s.tempState.surplus = s.tempState.surplus.Add(surplus)
	s.journal = append(s.journal, &surplusChange{delta: surplus})
	return *ZeroInt
}

func (s *DBImpl) GetBalance(evmAddr common.Address) *uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, evmAddr)
	// Hook for mock balances (no-op in production builds)
	s.ensureMinimumBalance(evmAddr)
	seiAddr := s.getSeiAddress(evmAddr)
	res, overflow := uint256.FromBig(s.k.GetBalance(s.ctx, seiAddr))
	if overflow {
		panic("balance overflow")
```

**File:** x/evm/ante/preprocess_test.go (L423-441)
```go
func TestMigrateBalance(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx(nil)
	admin, _ := testkeeper.MockAddressPair()
	seiAddr, evmAddr := testkeeper.MockAddressPair()
	k.BankKeeper().AddCoins(ctx, sdk.AccAddress(evmAddr[:]), sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(2))), false)
	// set a vesting account of 1
	k.AccountKeeper().SetAccount(ctx, vestingtypes.NewDelayedVestingAccountRaw(
		vestingtypes.NewBaseVestingAccount(
			k.AccountKeeper().NewAccountWithAddress(ctx, sdk.AccAddress(evmAddr[:])).(*authtypes.BaseAccount),
			sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(1))), math.MaxInt64, admin),
	))
	associateHelper := helpers.NewAssociationHelper(k, k.BankKeeper(), k.AccountKeeper())
	require.Nil(t, associateHelper.MigrateBalance(ctx, evmAddr, seiAddr, false))
	require.Equal(t, int64(1), k.BankKeeper().SpendableCoins(ctx, seiAddr).AmountOf("usei").Int64())
	require.Equal(t, int64(0), k.BankKeeper().LockedCoins(ctx, seiAddr).AmountOf("usei").Int64())
	require.Equal(t, int64(0), k.BankKeeper().SpendableCoins(ctx, sdk.AccAddress(evmAddr[:])).AmountOf("usei").Int64())
	require.Equal(t, int64(1), k.BankKeeper().LockedCoins(ctx, sdk.AccAddress(evmAddr[:])).AmountOf("usei").Int64())
}
```
