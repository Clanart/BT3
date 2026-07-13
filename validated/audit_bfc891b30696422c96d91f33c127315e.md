### Title
Non-EVM-Denom Cosmos Bank Tokens Permanently Locked on Contract Self-Destruct — (File: x/evm/statedb/statedb.go)

---

### Summary

When a contract self-destructs, `Commit()` in `statedb.go` only burns the EVM-denom balance before deleting the account. Non-EVM-denom tokens (IBC tokens, CosmWasm bridge tokens, etc.) held by the contract address are never drained. After `DeleteAccount` removes the account metadata, those tokens remain in the Cosmos bank module under the destroyed address with no account to claim them — permanently locked.

---

### Finding Description

In `Commit()`, the self-destruct branch explicitly handles only the EVM denom:

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

The guard condition is `if obj.selfDestructed` — a boolean flag — analogous to the external report's `if (!isNativeAsset)` check. Both checks are **necessary but not sufficient**: they correctly handle the primary case (EVM denom / native asset) but silently accept and permanently trap a second class of funds (non-EVM-denom tokens / arbitrary ETH).

`DeleteAccount` removes only the account metadata (auth/nonce/code):

```go
DeleteAccount(ctx sdk.Context, addr common.Address) error
``` [2](#0-1) 

The Cosmos bank module stores balances independently of account metadata. After `DeleteAccount`, the bank module still holds the non-EVM-denom tokens under the destroyed address, but there is no account to sign a spend transaction, no EVM code to call, and no recovery path in the protocol.

The `SelfDestruct` function itself only burns the EVM-denom balance at destruction time:

```go
balance := s.GetBalance(addr)
if balance.Sign() > 0 {
    s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)
}
``` [3](#0-2) 

`GetBalance` reads only the EVM denom:

```go
func (s *StateDB) GetBalance(addr common.Address) *uint256.Int {
    balance := s.keeper.GetBalance(s.ctx, sdk.AccAddress(addr.Bytes()), s.evmDenom)
    return &balance
}
``` [4](#0-3) 

Neither `SelfDestruct` nor the `Commit()` self-destruct branch queries or drains non-EVM-denom bank balances.

---

### Impact Explanation

Any non-EVM-denom Cosmos bank tokens (IBC-transferred assets, CosmWasm bridge tokens, or any denom other than `evmDenom`) held by a contract address at the time of self-destruct are permanently locked. The bank module retains the balance entry, but the account is deleted, making the funds irrecoverable. This constitutes a permanent loss of Cosmos bank funds triggered through Ethermint's stateDB/native action logic — matching the Critical impact class of "burn bypass … of Cosmos bank funds through … stateDB/native action logic."

---

### Likelihood Explanation

The entry path requires only an unprivileged actor:

1. A contract deployer deploys a contract that accepts IBC tokens (e.g., via a Cosmos `MsgSend` to the contract's bech32 address, which is valid on any Ethermint chain).
2. Users or integrations send IBC tokens to the contract address.
3. The contract executes `SELFDESTRUCT` (or `SELFDESTRUCT6780` on a newly-created contract in the same tx).
4. `Commit()` burns only the EVM denom and calls `DeleteAccount`.
5. IBC tokens are permanently orphaned.

No privileged role, validator collusion, or governance action is required. The attack is executable by any contract deployer on any Ethermint-based chain that supports IBC or multi-denom bank balances at EVM contract addresses.

---

### Recommendation

In the `Commit()` self-destruct branch, before calling `DeleteAccount`, iterate over **all** bank module balances for the contract address and burn each non-EVM-denom coin. The keeper interface already exposes `SubBalance`; a `GetAllBalances`-style query (or equivalent) should be added to the `Keeper` interface and called here:

```go
if obj.selfDestructed {
    cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
    cacheCtx, writeCache := s.origCtx.CacheContext()
    // Burn ALL denoms, not just evmDenom
    allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
    for _, coin := range allBalances {
        if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
            return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
        }
    }
    if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
        return errorsmod.Wrap(err, "failed to delete account")
    }
    writeCache()
}
```

---

### Proof of Concept

```
1. Deploy contract C on an Ethermint chain with IBC enabled.
2. Via Cosmos bank MsgSend, transfer 100 uatom (IBC denom) to C's bech32 address.
   → bank module records: C → 100 uatom
3. Call C.selfDestruct() (or deploy C with SELFDESTRUCT in constructor for EIP-6780).
4. EVM executes SELFDESTRUCT:
   - SelfDestruct() burns C's evmDenom balance (e.g., 0 aevmos).
   - Commit() checks obj.selfDestructed == true.
   - Commit() calls GetBalance(cacheCtx, C, evmDenom) → 0, skips SubBalance.
   - Commit() calls DeleteAccount(cacheCtx, C) → removes auth/nonce/code.
   - writeCache() flushes.
5. State after commit:
   - C's account metadata: deleted.
   - bank module balance for C: 100 uatom (still present, unreachable).
6. No transaction can spend the 100 uatom — the account no longer exists to sign,
   and no EVM code exists to call. Funds are permanently locked.
```

### Citations

**File:** x/evm/statedb/statedb.go (L210-213)
```go
func (s *StateDB) GetBalance(addr common.Address) *uint256.Int {
	balance := s.keeper.GetBalance(s.ctx, sdk.AccAddress(addr.Bytes()), s.evmDenom)
	return &balance
}
```

**File:** x/evm/statedb/statedb.go (L571-574)
```go
	balance := s.GetBalance(addr)
	if balance.Sign() > 0 {
		s.SubBalance(addr, balance, tracing.BalanceDecreaseSelfdestructBurn)
	}
```

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

**File:** x/evm/statedb/interfaces.go (L46-46)
```go
	DeleteAccount(ctx sdk.Context, addr common.Address) error
```
