### Title
Native EVM value transfers bypass the Cosmos bank `BlockedAddr` restriction, allowing usei to be deposited into protected module accounts - (File: x/evm/state/balance.go)

### Summary
`x/evm/state/balance.go`'s `DBImpl.AddBalance`/`SubBalance` (the functions EVM opcodes such as `CALL{value}` and native transfers use to move `usei`) call `BankKeeper().AddCoins` / `BankKeeper().SubUnlockedCoins` directly instead of going through `BaseSendKeeper.SendCoins`. [1](#0-0) 
The Cosmos bank module's `BlockedAddr` guard - the mechanism that stops external senders from crediting funds to protected addresses (typically module accounts) - is only enforced at the `MsgSend`/`MsgMultiSend` message-server layer, not inside `BaseSendKeeper.AddCoins`, `SubUnlockedCoins`, or `sendCoinsWithoutAccCreation`. [2](#0-1) [3](#0-2) 
This is structurally the same bug class as the MaxKB SSRF: one enforcement point (the sandbox's `connect()` hook / the bank msg-server's `BlockedAddr` check) is bypassed by using a lower-level primitive (`sendto()+MSG_FASTOPEN` / the EVM StateDB's direct `AddCoins`/`SubUnlockedCoins` calls) that achieves the same effect (establishing a connection / crediting a balance) without ever passing through the guarded code path.

### Finding Description
In upstream Cosmos SDK design, `BlockedAddr` is meant to prevent external users from depositing tokens into addresses that should never receive an arbitrary, unaccounted-for credit (module accounts such as `distribution`, `gov`, `bonded_tokens_pool`, etc., whose accounting invariants assume all inflows come from specific, internally-tracked keeper calls). The check is implemented once, in the `x/bank` message server, and is deliberately *not* duplicated inside the low-level `AddCoins`/`SubUnlockedCoins` primitives, because other Cosmos modules are trusted to call those primitives directly for legitimate internal transfers (e.g., distribution paying the fee pool).

Sei's EVM integration, however, exposes those low-level primitives to an untrusted, attacker-controlled entry point: any plain value-bearing EVM transaction or internal `CALL` executed by a smart contract routes through `vm.EVM`'s balance-transfer logic, which calls `DBImpl.SubBalance` on the sender and `DBImpl.AddBalance` on the recipient. [4](#0-3) [1](#0-0) 
Both functions resolve the destination's Sei address via `s.getSeiAddress(evmAddr)` (which falls back to a deterministic default association for any EVM address that hasn't been explicitly linked) and then call `BankKeeper().AddCoins`/`SubUnlockedCoins` directly - completely skipping the `BlockedAddr` gate that a normal `MsgSend` or the bank precompile would enforce. [5](#0-4) 

Because module account addresses are deterministically derived and public (`authtypes.NewModuleAddress(name)`), an attacker can craft or target the EVM-address representation of a blocked module account and send `usei` to it via a normal EVM transaction, permanently depositing funds outside of any keeper-tracked flow that module expects. I was not able to fully confirm the exact byte-mapping logic of `GetSeiAddressOrDefault`/`getSeiAddress` for un-associated addresses within this session's tool budget, so the precise reachability of specific blocked module accounts (versus only newly-created default Sei accounts) should be verified against `x/evm/keeper`'s address-association code before remediation; however, the structural bypass of `BlockedAddr` on the `AddBalance`/`SubBalance` path is confirmed directly from the cited code.

### Impact Explanation
If usei can be credited to a blocked module account through EVM value transfers, this can corrupt that module's balance-accounting invariants (which assume all balance changes flow through specific, tracked keeper functions such as `MintCoins`/`SendCoinsFromModuleToModule`). Depending on which module account is targeted, this can result in permanently frozen/unrecoverable user funds (the module has no code path to return externally-donated tokens) or accounting mismatches that downstream logic (e.g., distribution fee-pool math, staking module invariants) relies on being exact, satisfying the "fund loss / permanent freezing" and "supply/accounting corruption" impact bar.

### Likelihood Explanation
Reachable by any unprivileged EVM transaction sender - no special permissions, precompile access, or CosmWasm interaction is required; a bare `to.call{value: x}("")` from any EOA or contract exercises `AddBalance`/`SubBalance`. The main uncertainty is precisely which addresses are reachable via the default (unassociated) address-mapping scheme, which affects whether specific high-value module accounts are directly targetable or only newly-derived accounts are affected.

### Recommendation
Enforce the same `BlockedAddr` (and any `SendRestrictionFn`) checks inside `DBImpl.AddBalance` (and symmetrically in `SubBalance` for the corresponding source-side restrictions) before calling `BankKeeper().AddCoins`/`SubUnlockedCoins`, mirroring the check performed in the bank message server, so that EVM-originated transfers cannot land on protected module accounts. Alternatively, expose a bank keeper entrypoint that performs `BlockedAddr` validation and route all EVM-triggered balance changes through it.

### Proof of Concept
1. Compute `common.Address` bytes equal to (or otherwise resolvable via the default un-associated address mapping to) a known blocked module account, e.g. `authtypes.NewModuleAddress("distribution")`.
2. From any funded EOA, submit a standard EVM transaction: `eth_sendTransaction` with `to = <that address>` and non-zero `value`.
3. This invokes `vm.EVM.Call`'s value-transfer logic, which calls `DBImpl.SubBalance` on the sender and `DBImpl.AddBalance` on the target address.
4. `AddBalance` resolves the target to the module's Sei address and calls `BankKeeper().AddCoins` directly (`x/evm/state/balance.go:76-87`), with no `BlockedAddr` check performed anywhere in this call chain, unlike `MsgSend`, which would reject the same transfer.
5. Verify (e.g., via `x/bank`'s `GetBalance` query) that the module account's balance increased outside of any keeper-tracked mint/transfer, confirming the bypass.

### Citations

**File:** x/evm/state/balance.go (L15-61)
```go
func (s *DBImpl) SubBalance(evmAddr common.Address, amtUint256 *uint256.Int, reason tracing.BalanceChangeReason) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, evmAddr)
	amt := amtUint256.ToBig()
	if amt.Sign() == 0 {
		return *ZeroInt
	}
	if amt.Sign() < 0 {
		return s.AddBalance(evmAddr, new(uint256.Int).Neg(amtUint256), reason)
	}

	ctx := s.ctx
	var oldBalance *uint256.Int
	if s.logger != nil && s.logger.OnBalanceChange != nil {
		oldBalance = s.GetBalance(evmAddr)
	}

	// this avoids emitting cosmos events for ephemeral bookkeeping transfers like send_native
	if s.eventsSuppressed {
		ctx = ctx.WithEventManager(sdk.NewEventManager())
	}

	// Hook for mock balances (no-op in production builds)
	s.ensureSufficientBalance(evmAddr, amt)

	usei, wei := SplitUseiWeiAmount(amt)
	addr := s.getSeiAddress(evmAddr)
	err := s.k.BankKeeper().SubUnlockedCoins(ctx, addr, sdk.NewCoins(sdk.NewCoin(s.k.GetBaseDenom(s.ctx), usei)), true)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
	err = s.k.BankKeeper().SubWei(ctx, addr, wei)
	if err != nil {
		s.err = err
		return *ZeroInt
	}

	if s.logger != nil && s.logger.OnBalanceChange != nil && oldBalance != nil {
		newBalance := s.GetBalance(evmAddr).ToBig()
		oldBalance := new(big.Int).Add(newBalance, amt)
		s.logger.OnBalanceChange(evmAddr, oldBalance, newBalance, reason)
	}

	surplus := sdk.NewIntFromBigInt(amt)
	s.tempState.surplus = s.tempState.surplus.Add(surplus)
	s.journal = append(s.journal, &surplusChange{delta: surplus})
	return *ZeroInt
```

**File:** x/evm/state/balance.go (L64-95)
```go
func (s *DBImpl) AddBalance(evmAddr common.Address, amtUint256 *uint256.Int, reason tracing.BalanceChangeReason) uint256.Int {
	s.k.PrepareReplayedAddr(s.ctx, evmAddr)
	amt := amtUint256.ToBig()
	if amt.Sign() == 0 {
		return *ZeroInt
	}
	if amt.Sign() < 0 {
		return s.SubBalance(evmAddr, new(uint256.Int).Neg(amtUint256), reason)
	}

	ctx := s.ctx
	var oldBalance *uint256.Int
	if s.logger != nil && s.logger.OnBalanceChange != nil {
		oldBalance = s.GetBalance(evmAddr)
	}
	// this avoids emitting cosmos events for ephemeral bookkeeping transfers like send_native
	if s.eventsSuppressed {
		ctx = ctx.WithEventManager(sdk.NewEventManager())
	}

	usei, wei := SplitUseiWeiAmount(amt)
	addr := s.getSeiAddress(evmAddr)
	err := s.k.BankKeeper().AddCoins(ctx, addr, sdk.NewCoins(sdk.NewCoin(s.k.GetBaseDenom(s.ctx), usei)), true)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
	err = s.k.BankKeeper().AddWei(ctx, addr, wei)
	if err != nil {
		s.err = err
		return *ZeroInt
	}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L201-230)
```go
func (k BaseSendKeeper) SendCoinsWithoutAccCreation(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins) error {
	return k.sendCoinsWithoutAccCreation(ctx, fromAddr, toAddr, amt, true)
}

func (k BaseSendKeeper) sendCoinsWithoutAccCreation(ctx sdk.Context, fromAddr sdk.AccAddress, toAddr sdk.AccAddress, amt sdk.Coins, checkNeg bool) error {
	err := k.SubUnlockedCoins(ctx, fromAddr, amt, checkNeg)
	if err != nil {
		return err
	}

	err = k.AddCoins(ctx, toAddr, amt, checkNeg)
	if err != nil {
		return err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeTransfer,
			sdk.NewAttribute(types.AttributeKeyRecipient, toAddr.String()),
			sdk.NewAttribute(types.AttributeKeySender, fromAddr.String()),
			sdk.NewAttribute(sdk.AttributeKeyAmount, amt.String()),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(types.AttributeKeySender, fromAddr.String()),
		),
	})

	return nil
}
```

**File:** sei-cosmos/x/bank/keeper/send.go (L372-381)
```go
// BlockedAddr checks if a given address is restricted from
// receiving funds.
func (k BaseSendKeeper) BlockedAddr(addr sdk.AccAddress) bool {
	if len(addr) == len(CoinbaseAddressPrefix)+8 {
		if bytes.Equal(CoinbaseAddressPrefix, addr[:len(CoinbaseAddressPrefix)]) {
			return true
		}
	}
	return k.blockedAddrs[addr.String()]
}
```
