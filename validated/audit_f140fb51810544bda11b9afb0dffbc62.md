The code evidence is definitive. Let me confirm the exact lines before rendering judgment.

The `Commit()` path in `x/evm/statedb/statedb.go` lines 813–815 contains an explicit developer acknowledgment:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
```

And `DeleteAccount` in `x/evm/keeper/statedb.go` lines 189–222 only clears EVM storage and removes the auth account — it never calls `bankKeeper.SendCoins`, `bankKeeper.BurnCoins`, or any balance-draining operation for non-EVM denoms.

---

### Title
Non-EVM-Denom Bank Balances Orphaned on SELFDESTRUCT — (`x/evm/statedb/statedb.go`)

### Summary

When a contract that holds IBC or other non-EVM-denom tokens executes `SELFDESTRUCT`, `statedb.Commit()` burns only the EVM-denom balance and then calls `DeleteAccount()`, which removes the auth account and EVM storage but leaves all non-EVM bank balances intact at the address. With no auth account, those balances are permanently inaccessible — orphaned supply that no one can spend or recover.

### Finding Description

In `statedb.Commit()`, the self-destructed branch:

1. Calls `keeper.SubBalance(cacheCtx, cosmosAddr, evmDenomCoin)` — burns only the EVM denom.
2. Calls `keeper.DeleteAccount(cacheCtx, addr)` — removes auth account and EVM storage.
3. Calls `writeCache()` — commits both to the parent context. [1](#0-0) 

`DeleteAccount` itself performs no bank operations whatsoever: [2](#0-1) 

The developer comment at lines 813–815 explicitly concedes the gap: *"Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances."* [3](#0-2) 

### Impact Explanation

After `Commit()`:
- The auth account at the contract's Cosmos address is gone (`accountKeeper.RemoveAccount`).
- The bank module still holds the IBC (or other non-EVM-denom) coins at that address.
- No signer exists for the address; no module has custody; no governance path reclaims them.
- The coins are live in the bank supply but permanently unspendable — a supply/balance inconsistency and effective fund destruction for the holders.

The only theoretical recovery path is CREATE2 redeployment at the exact same address by the original deployer, which is not guaranteed and not a protocol-level safeguard.

### Likelihood Explanation

The preconditions are realistic in production Cosmos/EVM ecosystems:
- IBC tokens routinely flow to contract addresses (e.g., DEX pools, bridges, vaults).
- Contracts with `selfdestruct` (pre-EIP-6780) or same-transaction destruction (EIP-6780) exist.
- An attacker who controls or can call a contract's self-destruct path (e.g., a contract with an unguarded `kill()` function, or the deployer themselves) can trigger this with a standard EVM transaction — no privileged role required.

### Recommendation

In the `selfDestructed` branch of `Commit()`, iterate over **all** bank balances held by the contract address (not just the EVM denom) and burn or escrow each one before calling `DeleteAccount`. The Cosmos SDK `bankKeeper.GetAllBalances(ctx, cosmosAddr)` returns the full coin set; each non-zero balance should be burned via `bankKeeper.BurnCoins` (after sending to the module account) or transferred to a community pool, ensuring no orphaned supply remains.

### Proof of Concept

```
1. Deploy contract C via CREATE2 (salt S, deployer D).
2. IBC-transfer 1000 uatom to C's Cosmos address (valid IBC MsgTransfer to bech32(C)).
3. Call C.kill() (or any path that executes SELFDESTRUCT within the tx).
4. statedb.Commit() fires:
     SubBalance(C, evmDenom=0)   // no EVM balance to burn
     DeleteAccount(C)            // removes auth account, leaves uatom in bank
5. Query bank: bankKeeper.GetBalance(ctx, C, "uatom") == 1000  ← still there
6. Query auth: accountKeeper.GetAccount(ctx, C) == nil          ← no account
7. 1000 uatom are permanently inaccessible.
```

Integration assertion: after step 4, `bankKeeper.GetAllBalances(ctx, cosmosAddr)` must return an empty set for all denoms. Currently it does not for non-EVM denoms. [4](#0-3) [5](#0-4)

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

**File:** x/evm/keeper/statedb.go (L195-222)
```go
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
