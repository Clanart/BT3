### Title
Non-EVM-Native Token Balances Permanently Orphaned After SELFDESTRUCT — (`x/evm/statedb/statedb.go`)

---

### Summary

When a contract is self-destructed, `statedb.Commit()` only burns the EVM-native denom balance of the destroyed address. Non-EVM-native tokens (IBC coins, CosmWasm bridge tokens, or any other Cosmos bank-module coins) held by the contract are silently skipped and remain as orphaned bank balances at the now-deleted account address. Because the auth account is removed by `DeleteAccount`, no key can ever sign a transaction to move those coins, making them permanently unrecoverable. This is the direct Ethermint analog of the DaosLive `_mintToDao` pattern: value is credited to an address from which it can never be retrieved.

---

### Finding Description

In `statedb.Commit()`, the self-destruct branch explicitly handles only the EVM denom:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
    }
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
    return errorsmod.Wrap(err, "failed to delete account")
}
writeCache()
``` [1](#0-0) 

After `writeCache()` is called, the auth account is gone. Any bank-module coins denominated in anything other than the EVM denom (e.g., `ibc/...` denoms, wrapped tokens from a CosmWasm bridge) that were held at the contract's Cosmos address remain in the bank store with no controlling account. There is no sweep, no transfer to a recovery address, and no governance mechanism to drain them.

The `DeleteAccount` function confirms it only removes auth metadata and storage; it never touches the bank balance:

```go
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    ...
    k.accountKeeper.RemoveAccount(ctx, acct)
    ...
}
``` [2](#0-1) 

The `AddBalance` path that credits non-EVM tokens to a contract address uses real bank minting and sending (not virtual), so the coins are genuinely present in the bank store:

```go
func (k *Keeper) AddBalance(ctx sdk.Context, addr sdk.AccAddress, coin sdk.Coin) (uint256.Int, error) {
    ...
    if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil { ... }
    if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, addr, coins); err != nil { ... }
    ...
}
``` [3](#0-2) 

---

### Impact Explanation

Any non-EVM-native Cosmos bank coins (IBC tokens, bridged assets) held at a contract address at the time of SELFDESTRUCT are permanently locked. The bank store retains the balance entry, but the auth account that would authorize a `MsgSend` or any other spend is deleted. No recovery path exists in the protocol. This constitutes permanent, irreversible loss of valid user funds through the EVM stateDB commit path — matching the "valid user funds to be mis-accounted" criterion under the High allowed impact scope.

---

### Likelihood Explanation

In a live Cosmos/Ethermint chain:
- IBC transfers can be directed to any bech32 address, including a contract's Cosmos address.
- Precompiles or native-action hooks can credit non-EVM coins to a contract during EVM execution.
- Any contract with a `selfdestruct` path (including EIP-6780 same-transaction destruction) triggers this code path.

An attacker can deliberately exploit this: deploy a contract, attract IBC token deposits (e.g., by advertising a yield strategy), then call `selfdestruct`, permanently locking depositors' IBC tokens. Alternatively, a legitimate contract that accumulates IBC fees or rewards and is later upgraded via `selfdestruct`+redeploy will silently lose all non-EVM holdings. The entry path requires only a standard unprivileged EVM transaction.

---

### Recommendation

In the self-destruct branch of `statedb.Commit()`, iterate over **all** bank-module coin denominations held by the destroyed address (not just the EVM denom) and either burn them or transfer them to a designated recovery address (e.g., the community pool or a governance-controlled sink) before calling `DeleteAccount`. The `bankKeeper.GetAllBalances` method can enumerate all denominations held at the address.

---

### Proof of Concept

1. Deploy contract `C` on an Ethermint chain with IBC enabled.
2. IBC-transfer 1000 `ibc/ATOM` to `C`'s bech32 Cosmos address. The bank store now records `C → 1000 ibc/ATOM`.
3. Call `selfdestruct(beneficiary)` on `C` from within the same or a subsequent transaction.
4. `statedb.Commit()` runs: it burns any EVM-denom balance of `C`, then calls `DeleteAccount(C)`, removing the auth account.
5. Query `bankKeeper.GetAllBalances(ctx, C_cosmos_addr)`: returns `[1000 ibc/ATOM]`.
6. Attempt any spend from `C`'s address: fails — no auth account exists to sign.
7. The 1000 `ibc/ATOM` are permanently orphaned in the bank store with no controlling account and no protocol-level recovery mechanism. [1](#0-0) [2](#0-1)

### Citations

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** x/evm/keeper/statedb.go (L80-90)
```go
func (k *Keeper) AddBalance(ctx sdk.Context, addr sdk.AccAddress, coin sdk.Coin) (uint256.Int, error) {
	coins := sdk.NewCoins(coin)
	prevBalance := k.GetBalance(ctx, addr, coin.Denom)
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, addr, coins); err != nil {
		return uint256.Int{}, err
	}
	return prevBalance, nil
}
```

**File:** x/evm/keeper/statedb.go (L189-222)
```go
// DeleteAccount handles contract's suicide call:
// - remove code
// - remove states
// - remove auth account
//
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum accounts (contracts) can be selfdestructed
	_, ok := acct.(ethermint.EthAccountI)
	if !ok {
		return errorsmod.Wrapf(types.ErrInvalidAccount, "type %T, address %s", acct, addr)
	}

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		k.SetState(ctx, addr, key, nil)
		return true
	})

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.debugLog(ctx, "account suicided",
		"ethereum-address", addr,
		"cosmos-address", cosmosAddr,
	)

	return nil
```
