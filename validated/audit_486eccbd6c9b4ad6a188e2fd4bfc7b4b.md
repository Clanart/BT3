No vulnerability found for this question.

The reported bug class relies on ERC-20 style fee-on-transfer tokens where an external token contract can silently deduct a fee during `transfer`/`transferFrom`, causing the caller's assumed balance delta to diverge from the actual balance change. Chia's coin model has no structural analog to this: a coin's amount is a cryptographic property of the coin itself (part of its `name()`/id), and every spend is validated by consensus to conserve value — the sum of input coin amounts must equal the sum of output coin amounts plus fee, enforced during block validation, not by a separate token contract that can apply hidden deductions.

I checked the closest candidates:
- CAT transfers (`chia/wallet/cat_wallet/cat_wallet.py` `generate_unsigned_spendbundle`) and CR-CAT transfers (`chia/wallet/vc_wallet/cr_cat_wallet.py` `_generate_unsigned_spendbundle`) compute `selected_cat_amount` and assert it against `starting_amount`/`payment_amount` directly from CLVM-created coin amounts, which are exactly what will exist on-chain — there is no intermediate "fee" that can reduce the amount actually received. [1](#0-0) [2](#0-1) 
- The closest thing to "funding a pool" in Chia is `PoolWallet` reward absorption, where the pool reward puzzle forwards `reward_amount` exactly to the target puzzle hash with no fee-skimming step. [3](#0-2)  `claim_pool_rewards` sums `coin_record.coin.amount` directly from confirmed on-chain coins for `total_amount`, which reflects the true, already-consensus-validated amount — there is no discrepancy between "amount sent" and "amount received" to exploit. [4](#0-3) 

Since coin amounts in Chia cannot be partially consumed by a third-party fee during transfer — any such deduction would have to be an explicit CLVM condition creating a smaller output coin, which is visible and verifiable rather than a hidden balance mismatch — this bug class has no reachable, unprivileged analog in the in-scope Chia codebase.

### Citations

**File:** chia/wallet/cat_wallet/cat_wallet.py (L796-815)
```python
        payment_amount: int = sum(p.amount for p in payments)
        starting_amount: int = payment_amount - extra_delta
        if coins is None:
            cat_coins_set = await self.select_coins(
                uint64(starting_amount),
                action_scope,
            )
        else:
            cat_coins_set = coins

        cat_coins = list(
            sorted(
                cat_coins_set,
                key=lambda c: c.name() == action_scope.config.tx_config.coin_selection_config.primary_coin,
                reverse=True,
            )
        )

        selected_cat_amount = sum(c.amount for c in cat_coins)
        assert selected_cat_amount >= starting_amount
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L412-427)
```python
        payment_amount: int = sum(p.amount for p in payments)
        starting_amount: int = payment_amount - extra_delta
        if coins is None:
            cat_coins = list(
                await self.select_coins(
                    uint64(starting_amount),
                    action_scope,
                )
            )
        else:
            cat_coins = list(coins)

        cat_coins = sorted(cat_coins, key=Coin.name)  # need determinism because we need definitive origin coin

        selected_cat_amount = sum(c.amount for c in cat_coins)
        assert selected_cat_amount >= starting_amount
```

**File:** chia/pools/forward_to_pool_puzzle_hash_dpuz.clsp (L1-6)
```text
; Intended to be run by a pooling reward when being claimed by a pool
;
; This puzzle checks the existing amount and then forwards the whole amount to a dedicated puzzle hash with dedicated memos
(mod (REWARD_HASH REWARD_REST reward_amount)
  (list (list 73 reward_amount) (c 51 (c REWARD_HASH (c reward_amount REWARD_REST))))
)
```

**File:** chia/pools/pool_wallet.py (L763-774)
```python
            absorb_spend: list[CoinSpend] = create_absorb_spend(
                last_solution,
                current_state.current,
                current_state.launcher_coin,
                coin_to_height_farmed[coin_record.coin],
                self.wallet_state_manager.constants.GENESIS_CHALLENGE,
                delayed_seconds,
                delayed_puzhash,
            )
            last_solution = absorb_spend[0]
            all_spends += absorb_spend
            total_amount += coin_record.coin.amount
```
