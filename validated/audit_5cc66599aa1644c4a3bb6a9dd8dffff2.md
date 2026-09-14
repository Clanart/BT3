### Title
Wallet displays a failed spend bundle as permanently "Confirmed" without any on-chain coin spend - ([File: chia/wallet/wallet_transaction_store.py])

### Summary
`WalletTransactionStore.increment_sent()` marks a `TransactionRecord` as `confirmed=True` with `confirmed_at_height=uint32(0)` once a transaction exhausts its retry attempts and is deemed invalid, even though the associated coins were never actually spent on-chain. This mirrors the ONOS CVE-2022-29607 bug class: a state transition is reported as terminally "confirmed"/"installed" while the underlying resource (an intent's flow rule / here, an on-chain coin spend) was never realized, misleading the operator (here, the wallet user or an offer counterparty relying on wallet-reported status).

### Finding Description
When a spend bundle repeatedly fails admission (e.g., `MEMPOOL_NOT_INITIALIZED`, or any error outside the two explicitly tolerated fee-related errors), `increment_sent()` computes `tx.is_valid()` and, if it becomes `False` after `minimum_send_attempts` (6) failed peer attempts, forcibly rewrites the record: [1](#0-0) 

```
tx: TransactionRecord = dataclasses.replace(current, sent=sent_count, sent_to=sent_to)
if not tx.is_valid():
    # if the tx is not valid due to repeated failures, we will confirm that we can't spend it
    log.info(f"Marking tx={tx.name} as confirmed but failed, since it is not spendable due to errors")
    tx = dataclasses.replace(tx, confirmed=True, confirmed_at_height=uint32(0))
await self.add_transaction_record(tx)
```

The comment in the code ("we will confirm that we can't spend it") reveals the intent was to signal "terminal/failed", but the mechanism used to signal that is the same `confirmed=True` flag that the rest of the codebase (and UI/RPC) uses to mean "this transaction's coins were spent and included in a block". There is no distinct terminal-failure state; `confirmed` is overloaded.

`TransactionRecordOld.is_valid()` treats any `sent_to` history containing a `SUCCESS` mempool ack as valid regardless of `confirmed`: [2](#0-1) 

The `confirmed`/`confirmed_at_height` fields are consumed throughout the wallet and RPC/CLI surface as proof of on-chain inclusion — e.g. `set_confirmed()` uses the exact same fields to mark a genuinely-included transaction: [3](#0-2) 

Because `confirmed_at_height=0` is also used elsewhere in the codebase as a legitimate/sentinel sync value (e.g. `WalletStateManager`/`clawback_manager.py`/`vc_wallet.py` use `confirmed_at_height == 0` checks for different purposes), a caller inspecting only `record.confirmed` (True) cannot distinguish "actually included on-chain" from "gave up retrying, injecting a fabricated confirmed record." Any code path, RPC response, or CLI/GUI element that surfaces `confirmed` as a boolean will report this failed transaction identically to a genuinely confirmed one.

### Impact Explanation
A wallet user (or an offer/trade counterparty who relies on the sender showing "Confirmed" status) can be misled into believing a payment or trade settlement occurred, when the coins in question were never spent and remain fully spendable by the original owner. This is analogous to the ONOS defect: a state is reported as durably "installed"/"confirmed" while no underlying resource change (flow rule / coin spend) exists, misleading whoever consumes that status for a decision (e.g., releasing goods/services, closing a trade as done, or reconciling wallet balances against reported transaction history). This does not directly move funds by itself, but it is a state-integrity/reporting defect reachable purely by normal wallet usage (repeated mempool rejections, which an attacker can also induce by fee-griefing or by causing legitimate rejections), and it can be leveraged as a social/reconciliation attack vector in trade or payment-confirmation workflows.

### Likelihood Explanation
This path triggers automatically whenever a spend bundle fails to be accepted by `minimum_send_attempts` (6) peers with a non-fee-related error — no special privilege is needed; the condition can be reached by an ordinary transaction that experiences repeated node-side rejections (e.g., transient mempool state, conflicting spends, or a counterpart deliberately causing rejections across multiple peer connections). It is a background/internal state-write, not a manual admin action, so it is highly reachable from unprivileged wallet operation.

### Recommendation
Do not overload the `confirmed`/`confirmed_at_height` fields to represent "gave up retrying." Introduce an explicit terminal-failure status (or reuse `TradeStatus`-like enum) distinct from on-chain confirmation, and update all UI/RPC/CLI consumers (`GetTransactionCMD`, `push_transactions` responses, trade-manager coin-farmed logic, balance calculations) to check that distinct field before treating a transaction as chain-confirmed. At minimum, ensure `confirmed_at_height=0` combined with `confirmed=True` is never surfaced to a user as "Confirmed" without an accompanying explicit "not actually included, no spend occurred" qualifier.

### Proof of Concept
1. Create and sign a transaction via `Wallet.generate_signed_transaction` (or any wallet spend path), obtaining a `TransactionRecord`.
2. Cause the associated peer(s)/full node(s) to reject the spend bundle with a non-fee `Err` at least `minimum_send_attempts` (6) times — this can occur naturally (e.g., a spend bundle repeatedly hitting `MEMPOOL_CONFLICT`/other rejects) or be induced by an attacker who can trigger mempool rejections for the target's outgoing spend across enough peer connections, as exercised in `test_increment_sent_error`: [4](#0-3) 
3. After the 6th failed `increment_sent()` call, query `get_transaction_record(tx_id)` (as done via RPC `get_transaction` / CLI `GetTransactionCMD`) — the record now reports `confirmed=True`, `confirmed_at_height=0`, identical in shape to a genuinely on-chain-confirmed transaction, despite the underlying coin never being spent and remaining fully spendable.
4. A counterparty or the user, observing `confirmed=True`, believes the spend completed, while the coin is actually still unspent — the discrepancy between wallet-reported state and actual coin-store/chain state is the vulnerability, directly analogous to CVE-2022-29607's "INSTALLED without a flow rule."

Note: I was unable to fully trace every downstream UI/CLI code path (e.g., exact `chia/cmds/wallet.py` rendering logic for `Status:`) within the available indexed content, so the precise wording of the misleading CLI/GUI output could not be directly confirmed from the index; a Devin session with full repository access would be needed to verify the exact display strings and any additional consumers of `TransactionRecord.confirmed` that might already special-case `confirmed_at_height == 0`.

### Citations

**File:** chia/wallet/wallet_transaction_store.py (L160-171)
```python
    async def set_confirmed(self, tx_id: bytes32, height: uint32):
        """
        Updates transaction to be confirmed.
        """
        current: TransactionRecord | None = await self.get_transaction_record(tx_id)
        if current is None:
            return None
        if current.confirmed_at_height == height:
            return
        tx: TransactionRecord = dataclasses.replace(current, confirmed_at_height=height, confirmed=True)
        await self.add_transaction_record(tx)
        self.unconfirmed_txs.remove(get_light_transaction_record(current))
```

**File:** chia/wallet/wallet_transaction_store.py (L204-209)
```python
        tx: TransactionRecord = dataclasses.replace(current, sent=sent_count, sent_to=sent_to)
        if not tx.is_valid():
            # if the tx is not valid due to repeated failures, we will confirm that we can't spend it
            log.info(f"Marking tx={tx.name} as confirmed but failed, since it is not spendable due to errors")
            tx = dataclasses.replace(tx, confirmed=True, confirmed_at_height=uint32(0))
        await self.add_transaction_record(tx)
```

**File:** chia/wallet/transaction_record.py (L82-92)
```python
    def is_valid(self) -> bool:
        if len(self.sent_to) < minimum_send_attempts:
            # we haven't tried enough peers yet
            return True
        if any(x[1] == MempoolInclusionStatus.SUCCESS.value for x in self.sent_to):
            # we managed to push it to mempool at least once
            return True
        if any(x[2] in {Err.INVALID_FEE_LOW_FEE.name, Err.INVALID_FEE_TOO_CLOSE_TO_ZERO.name} for x in self.sent_to):
            # we tried to push it to mempool and got a fee error so it's a temporary error
            return True
        return False
```

**File:** chia/_tests/wallet/test_transaction_store.py (L137-152)
```python
@pytest.mark.anyio
async def test_increment_sent_error() -> None:
    async with DBConnection(1) as db_wrapper:
        store = await WalletTransactionStore.create(db_wrapper, MINIMUM_CONFIG)

        await store.add_transaction_record(tr1)
        tr = await store.get_transaction_record(tr1.name)
        assert tr is not None
        assert tr.sent == 0
        assert tr.sent_to == []

        await store.increment_sent(tr1.name, "peer1", MempoolInclusionStatus.FAILED, Err.MEMPOOL_NOT_INITIALIZED)
        tr = await store.get_transaction_record(tr1.name)
        assert tr is not None
        assert tr.sent == 1
        assert tr.sent_to == [("peer1", uint8(3), "MEMPOOL_NOT_INITIALIZED")]
```
