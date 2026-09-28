### Title
Deposits made via coinbase transaction outputs are permanently skipped by the Bitcoin scanner and never credited - (File: processor/src/networks/bitcoin.rs)

### Summary
The analog of "providers assume no rewards and leave `claim` empty" is `Bitcoin::get_outputs` assuming all deposits arrive in non-coinbase transactions. It unconditionally iterates `block.txdata[1 ..]`, permanently skipping `txdata[0]` (the coinbase). Any output paying a registered vault/external/branch/change/forward address inside a coinbase transaction is invisible to the processor, never emitted as a `ScannerEvent::Block` output, and never credited — even though the underlying `Scanner::scan_block` in `bitcoin_serai` is fully capable of returning coinbase outputs (it scans the whole block and merely documents that coinbase outputs need a maturity post-pass).

### Finding Description
In `get_outputs`, the code comments "Skip the coinbase transaction which is burdened by maturity" and loops over `&block.txdata[1 ..]`:

- `processor/src/networks/bitcoin.rs:686-700`: `for tx in &block.txdata[1 ..] { for output in scanner.scan_transaction(tx) { ... } }` — index 0 is never scanned.

By contrast, the lower-level `Scanner::scan_block` in `networks/bitcoin/src/wallet/mod.rs:221-227` scans *all* transactions, including the coinbase, and its doc-comment explicitly notes the correct remediation: "If received outputs must be immediately spendable, a post-processing pass is needed to remove those outputs. Alternatively, scan_transaction can be called on `block.txdata[1 ..]`." The processor took the "skip" option but applies it destructively: a coinbase output is dropped at scan time, and since the scanner's seen-outputs dedup/last-scanned-block bookkeeping (`processor/src/multisigs/scanner.rs:593-620`) marks the block as processed, the coinbase output is never revisited after it matures (~100 blocks). There is no delayed rescan path — the funds are simply never observed by the processor.

This mirrors the Derby finding structurally: a code path that hardcodes "this class of value doesn't exist here" (empty `claim()` / skip coinbase), permanently forfeiting value that legitimately exists.

### Impact Explanation
Bitcoin mining pools routinely pay out rewards directly in the coinbase transaction to miner-specified addresses (e.g., pool payout schemes like FPPS/PPLNS outputs, or solo miners). If a user or an integrated service arranges a payout to a Serai vault/external address — which is a completely valid, normal way to receive BTC — the deposit is never credited on the Serai side while the BTC sits provably locked to the multisig's key on-chain. The funds are unrecoverable through the normal protocol path: they were never registered as outputs, so no scheduler will ever build a spend of them, leaving them stranded (effectively burned) unless the multisig manually intervenes. This is "funds received but never recognized," a permanent loss of user funds — comparable in impact to the unclaimable-rewards loss in the original report.

### Likelihood Explanation
Any unprivileged party can trigger this by causing BTC to be paid to a Serai address in a coinbase transaction — most realistically by configuring a mining pool payout address (pools commonly support arbitrary payout addresses and pay via coinbase). It does not require controlling a validator, node, or peer. The likelihood is lower than an ordinary-deposit bug because most deposits come via regular transactions, but it requires no adversarial capability beyond choosing where mining rewards are sent, and failure is silent and permanent — warranting Medium severity.

### Recommendation
Do not silently drop coinbase outputs. Either:
1. Scan `block.txdata[0]` as well and defer maturity-bound outputs: record coinbase-derived outputs in the scanner DB keyed by their outpoint plus the block height, and emit them as `ScannerEvent::Block` outputs only once `block_being_scanned - coinbase_height >= 100` (maturity), so they are credited after becoming spendable; or
2. If coinbase deposits are intentionally unsupported, detect coinbase outputs paying registered scripts and emit an explicit warning/event and document the behavior as a rejected-deposit path, rather than silently discarding them — ideally rejecting such deposits at the address layer is impossible, so option 1 (credit after maturity) is the correct fix.

### Proof of Concept
1. Register a key with the processor `Scanner` so `get_outputs` watches its `external` P2TR script (`processor/src/networks/bitcoin.rs:686-687` builds the script set via `scanner(key)`).
2. Mine a regtest/mainnet block whose coinbase transaction (`block.txdata[0]`) contains an output `TxOut { value: 5_000_000_000, script_pubkey: <external P2TR script for the multisig key> }`. On regtest this is `generatetoaddress` pointed at the Serai P2TR address — exactly what `send_and_get_output` in `networks/bitcoin/tests/wallet.rs:40-77` does, which is why the wallet-level `scan_block` test sees the coinbase output while the processor never would.
3. Call `Bitcoin::get_outputs(&block, key)`. The loop `for tx in &block.txdata[1 ..]` (`processor/src/networks/bitcoin.rs:691`) skips index 0, so the coinbase output is absent from the returned `Vec<Output>` even though `Scanner::scan_transaction(block.txdata[0])` would return it.
4. The scanner then persists the block as scanned (`processor/src/multisigs/scanner.rs:537-540` saves the block; `:593-620` dedups outputs by ID going forward), so the matured coinbase output is never re-emitted in later blocks. Result: 50 BTC-equivalent satoshis provably owned by the multisig key are permanently uncredited and unscheduled for spending — the "reward" the provider never claims.