## Finding: Clawback auto-claim batch can be permanently poisoned by an attacker-controlled coin, blocking legitimate claims

### Title
Malicious clawback sender can block the wallet's clawback auto-claim queue - (File: `chia/wallet/clawback_manager.py`)

### Summary
The Rollovers-Queue bug class ("one non-atomic recipient failure blocks all future queue processing for everyone else") has a direct analog in Chia's clawback auto-claim system. `ClawbackManager.auto_claim_coins()` batches multiple unrelated clawback coins (potentially sent by many different, unrelated senders) into a single `spend_clawback_coins()` call, and only resets its batch accumulator on the success path — never on failure.

### Finding Description
`ClawbackManager.auto_claim_coins` iterates over a wallet's unspent clawback coins and accumulates them into a shared `clawback_coins` dict, flushing (spending) the batch once it reaches `auto_claim_batch_size`: [1](#0-0) 

The `await self.spend_clawback_coins(clawback_coins, ...)` call and the `clawback_coins = {}` reset are both inside the same `try` block. If `spend_clawback_coins` raises (e.g. via `assert derivation_record is not None` or an unhandled error out of `create_tandem_xch_tx` when building the fee spend) the exception is swallowed by the surrounding `except Exception` — but because the reset line never executes on that path, the already-accumulated (and poisoned) batch is **not cleared**. The dict retains every coin gathered so far, including the coin whose data caused the failure. [2](#0-1) 

Because `clawback_coins` is not attacker-scoped — any external party can create and send a standard XCH clawback-timelocked coin to a victim's wallet (the recipient side of a clawback transfer is exactly the untrusted, permissionless action a "spend-bundle submitter" performs) — a single malicious sender can craft/trigger conditions that make the batched spend fail once that coin is included. On every subsequent coin processed in the same `auto_claim_coins()` invocation, the poisoned entry remains in the dict, so every later batch-flush attempt re-includes the bad coin and keeps failing, meaning none of the *other* legitimate clawback coins from unrelated senders get claimed in that pass — mirroring `mintRollovers()`'s failure mode where one bad recipient blocks the whole non-atomic batch.

### Impact Explanation
This is a spend-triggered transaction-processing halt: the auto-claim mechanism (and the identical batching pattern reachable via the `spend_clawback_coins` RPC in `chia/wallet/wallet_rpc_api.py:1479-1524`) can be made to repeatedly fail for a victim wallet, indefinitely delaying claim of legitimate, unrelated clawback payments sent by third parties, funds that should be claimable become stuck as long as the poisoned coin remains in the unspent set (which it will, since the failure is never resolved and the coin keeps being picked up on every periodic run).

### Likelihood Explanation
Likelihood is moderate: it requires (a) an attacker able to send a clawback coin to the victim (trivial, permissionless), and (b) a code path in `spend_clawback_coins` that can raise outside of its internal per-coin `try/except` (e.g., the `assert derivation_record is not None` invariant, or an exception from `create_tandem_xch_tx` when `auto_claim_tx_fee` is nonzero). The bug (missing reset-on-failure) itself is always present.

### Recommendation
Reset `clawback_coins = {}` (or otherwise drop the specific failing coin(s)) whenever `spend_clawback_coins` fails, and isolate failures per-coin rather than per-batch so that one bad coin cannot repeatedly poison an accumulating shared batch dictionary across loop iterations.

### Proof of Concept
1. Attacker sends a normal clawback-locked XCH coin to victim's wallet with metadata/conditions engineered to cause `spend_clawback_coins` to raise once the timelock expires and it enters a batch (e.g. by manipulating fee/derivation-record edge cases exercised at `chia/wallet/clawback_manager.py:299`).
2. Victim's `auto_claim_coins()` runs periodically (or via `spend_clawback_coins` RPC) and includes the malicious coin in a batch that reaches `auto_claim_batch_size`.
3. `spend_clawback_coins` raises; the exception is caught by `auto_claim_coins`'s outer `try/except`, but `clawback_coins` is never cleared (`chia/wallet/clawback_manager.py:200-201` never reached).
4. Every subsequent legitimate clawback coin appended in the same run re-triggers a batch flush containing the still-present poisoned coin, so it also fails — none of the victim's legitimate clawback deposits from other senders get claimed in that run, and the pattern recurs on the next periodic auto-claim cycle.

### Citations

**File:** chia/wallet/clawback_manager.py (L191-205)
```python
        for coin in unspent_coins.records:
            try:
                metadata = coin.parsed_metadata()
                assert isinstance(metadata, ClawbackMetadata)
                if await metadata.is_recipient(self.puzzle_store):
                    coin_timestamp = await self.timestamp_for_height(coin.confirmed_block_height)
                    if current_timestamp - coin_timestamp >= metadata.time_lock:
                        clawback_coins[coin.coin] = metadata
                        if len(clawback_coins) >= self.auto_claim_batch_size:
                            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
                            clawback_coins = {}
            except Exception as e:
                self.log.error(f"Failed to claim clawback coin {coin.coin.name().hex()}: %s", e)
        if len(clawback_coins) > 0:
            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
```

**File:** chia/wallet/clawback_manager.py (L207-266)
```python
    async def spend_clawback_coins(
        self,
        clawback_coins: dict[Coin, ClawbackMetadata],
        fee: uint64,
        action_scope: WalletActionScope,
        force: bool = False,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        assert len(clawback_coins) > 0
        coin_spends: list[CoinSpend] = []
        message = std_hash(b"".join([c.name() for c in clawback_coins.keys()]))
        derivation_record = None
        amount = uint64(0)
        for coin, metadata in clawback_coins.items():
            try:
                self.log.info(f"Claiming clawback coin {coin.name().hex()}")
                # Get incoming tx
                incoming_tx = await self.transaction_store.get_transaction_record(coin.name())
                assert incoming_tx is not None, f"Cannot find incoming tx for clawback coin {coin.name().hex()}"
                if incoming_tx.sent > 0 and not force:
                    self.log.error(
                        f"Clawback coin {coin.name().hex()} is already in a pending spend bundle. {incoming_tx}"
                    )
                    continue

                recipient_puzhash = metadata.recipient_puzzle_hash
                sender_puzhash = metadata.sender_puzzle_hash
                is_recipient: bool = await metadata.is_recipient(self.puzzle_store)
                if is_recipient:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(recipient_puzhash)
                else:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(sender_puzhash)
                assert derivation_record is not None
                amount = uint64(amount + coin.amount)
                # Remove the clawback hint since it is unnecessary for the XCH coin
                memos: list[bytes] = [] if len(incoming_tx.memos) == 0 else next(iter(incoming_tx.memos.items()))[1][1:]
                inner_puzzle = self.xch_wallet.puzzle_for_pk(derivation_record.pubkey)
                inner_solution = self.xch_wallet.make_solution(
                    primaries=[
                        CreateCoin(
                            derivation_record.puzzle_hash,
                            uint64(coin.amount),
                            memos,  # Forward memo of the first coin
                        )
                    ],
                    conditions=(
                        extra_conditions
                        if len(coin_spends) > 0 or fee == 0
                        else (*extra_conditions, CreateCoinAnnouncement(message))
                    ),
                )
                coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
                coin_spends.append(coin_spend)
                # Update incoming tx to prevent double spend and mark it is pending
                await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
            except Exception as e:
                self.log.error(f"Failed to create clawback spend bundle for {coin.name().hex()}: {e}")
        if len(coin_spends) == 0:
            return
        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
```
