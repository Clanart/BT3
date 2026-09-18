## Finding

### Title
SELFDESTRUCT resolves the wrong bank account after deleting the EVM↔Sei address association, letting a contract's real balance escape being swept - (File: `x/evm/state/state.go`)

### Summary
`DBImpl.SelfDestruct` deletes the EVM-address-to-Sei-address mapping **before** it reads and debits the destructing account's balance. Every subsequent balance lookup re-derives the Sei address from the (now-deleted) mapping, so it silently falls back to a different, unfunded "default" address instead of the address that actually holds the funds. This is the same bug class as CVE-2022-48754 (`phylib_detach`): a resource (`put_device()` / here, the address-association record) is torn down before a dependent operation (`phy_device_reset()` / here, `GetBalance`+`SubBalance`) that still needs it.

### Finding Description
`SelfDestruct` is implemented as: [1](#0-0) 

```go
func (s *DBImpl) SelfDestruct(acc common.Address) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, acc)
	if seiAddr, ok := s.k.GetSeiAddress(s.ctx, acc); ok {
		// remove the association
		s.k.DeleteAddressMapping(s.ctx, seiAddr, acc)
	}
	b := s.GetBalance(acc)
	s.SubBalance(acc, b, tracing.BalanceDecreaseSelfdestruct)

	// mark account as self-destructed
	s.MarkAccount(acc, AccountDeleted)
	return *b
}
```

`DeleteAddressMapping` removes both directions of the association from the same `sdk.Context` KV-store that the rest of the call operates on: [2](#0-1) 

`GetBalance` and `SubBalance` both resolve the account to debit via `getSeiAddress`, which calls `GetSeiAddressOrDefault`: [3](#0-2) [4](#0-3) 

`GetSeiAddressOrDefault` falls back to a direct byte-cast of the EVM address whenever the mapping lookup fails: [5](#0-4) 

Because the mapping was already deleted two lines earlier in the *same* function, the lookup inside `GetBalance(acc)` and `SubBalance(acc, b, ...)` fails and both calls operate on `sdk.AccAddress(evmAddr[:])` — a different account than the real, previously-associated `seiAddr` that actually holds the account's `usei`/wei balance. The real `seiAddr`'s balance is left completely untouched, while `b` (the amount reported as "swept") is read from an unrelated, typically zero-balance, direct-cast address.

### Impact Explanation
The purpose of `SelfDestruct` is to zero out the destructing account's EVM-visible balance and move the swept value to the coinbase/fee collector (per go-ethereum SELFDESTRUCT semantics, as exercised by `TestSelfDestructAssociated`). With the association deleted first:
- The genuinely funded Sei account (`seiAddr`) never has its balance debited, because `GetBalance`/`SubBalance` are silently redirected to the wrong (default-cast) address.
- The reported swept amount `b` is read from the wrong address and is very likely `0`, so no funds move to the fee collector even though the contract is marked self-destructed.
- The real funds remain sitting in `seiAddr`, but the EVM-address mapping that would let the EVM layer account for or reach them again has just been deleted, so from the EVM state-machine's point of view those funds simply vanish from tracked EVM balance/state consistency while still being spendable via the Cosmos bank module under the original Sei address.

This breaks balance-conservation invariants the EVM module relies on (surplus/wei tracking, receipt gas refunds, coinbase accounting) and lets a contract deployer/caller manipulate SELFDESTRUCT accounting so that funds are not correctly swept to the fee collector — a fund-accounting/fee-abuse bug reachable by any address association + contract deployer.

### Likelihood Explanation
Reachable by any unprivileged EVM caller: associate a Sei address with an EVM address (a normal, permissionless action), fund that Sei address, deploy a contract at (or associate) that address, and call `SELFDESTRUCT`/`SELFDESTRUCT6780` from a contract created in the same transaction/block (or via `EIP-6780` "same block" path). No validator or governance privilege is required, and the ordering bug fires on every self-destruct of an associated account.

### Recommendation
Capture the Sei address (or perform the balance read/debit) **before** deleting the address mapping, e.g.:
```go
func (s *DBImpl) SelfDestruct(acc common.Address) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, acc)
	seiAddr, hadAssociation := s.k.GetSeiAddress(s.ctx, acc)

	b := s.GetBalance(acc)              // resolve/debit while mapping still exists
	s.SubBalance(acc, b, tracing.BalanceDecreaseSelfdestruct)

	if hadAssociation {
		s.k.DeleteAddressMapping(s.ctx, seiAddr, acc) // remove association last
	}

	s.MarkAccount(acc, AccountDeleted)
	return *b
}
```
This mirrors the upstream kernel fix: perform the operation that depends on the resource before releasing/tearing it down.

### Proof of Concept
1. Create two accounts: `seiAddr` (funded, e.g. via `MintCoins`/`SendCoinsFromModuleToAccount`) and `evmAddr`.
2. Call `SetAddressMapping(ctx, seiAddr, evmAddr)` to associate them.
3. Fund `seiAddr` with a nonzero `usei` balance.
4. Mark `evmAddr` as `Created` in the current block (e.g. via `CreateAccount`), then invoke `SelfDestruct(evmAddr)` (directly, or via a contract executing `SELFDESTRUCT`/6780 in the creation transaction).
5. Observe:
   - `GetSeiAddress(ctx, evmAddr)` now returns `false` (mapping deleted).
   - `k.BankKeeper().GetBalance(ctx, seiAddr, denom)` still shows the original, non-zero balance — it was never debited.
   - The value returned by `SelfDestruct` / credited to the fee collector is `0` (or whatever balance happens to sit at the unrelated direct-cast address), not the account's real balance.

This reproduces the "use resource after it was released" ordering bug and demonstrates that self-destruct balance sweeping silently fails for associated accounts.

### Citations

**File:** x/evm/state/state.go (L86-98)
```go
func (s *DBImpl) SelfDestruct(acc common.Address) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, acc)
	if seiAddr, ok := s.k.GetSeiAddress(s.ctx, acc); ok {
		// remove the association
		s.k.DeleteAddressMapping(s.ctx, seiAddr, acc)
	}
	b := s.GetBalance(acc)
	s.SubBalance(acc, b, tracing.BalanceDecreaseSelfdestruct)

	// mark account as self-destructed
	s.MarkAccount(acc, AccountDeleted)
	return *b
}
```

**File:** x/evm/keeper/address.go (L24-28)
```go
func (k *Keeper) DeleteAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Delete(types.EVMAddressToSeiAddressKey(evmAddress))
	store.Delete(types.SeiAddressToEVMAddressKey(seiAddress))
}
```

**File:** x/evm/keeper/address.go (L58-64)
```go
func (k *Keeper) GetSeiAddressOrDefault(ctx sdk.Context, evmAddress common.Address) sdk.AccAddress {
	addr, ok := k.GetSeiAddress(ctx, evmAddress)
	if ok {
		return addr
	}
	return sdk.AccAddress(evmAddress[:])
}
```

**File:** x/evm/state/balance.go (L103-122)
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
	}
	if res == nil {
		return uint256.NewInt(0)
	}
	return res
}
```

**File:** x/evm/state/balance.go (L147-152)
```go
func (s *DBImpl) getSeiAddress(evmAddr common.Address) sdk.AccAddress {
	if s.coinbaseEvmAddress.Cmp(evmAddr) == 0 {
		return s.coinbaseAddress
	}
	return s.k.GetSeiAddressOrDefault(s.ctx, evmAddr)
}
```
