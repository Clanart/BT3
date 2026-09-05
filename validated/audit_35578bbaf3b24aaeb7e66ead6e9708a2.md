### Title
Front-running of PoX-4 `signer-sig` authorizations permanently invalidates a stacker's pending stacking transaction (auth-id griefing/freezing) - (File: `stackslib/src/chainstate/stacks/boot/pox-4.clar`)

### Summary
The PoX-4 contract's signer-key authorization mechanism (`signer-sig`) is structurally identical to an ERC20 "permit": a stacker obtains an off-chain signature from their signer-key that is submitted as a plain, public function argument to `stack-stx`, `stack-extend`, `stack-increase`, `stack-aggregation-commit-indexed`, `stack-aggregation-increase`, or `delegate-stack-stx`. Because the signature payload is not bound to `tx-sender`, and because the replay-protection map keys only on `{signer-key, reward-cycle, period, topic, pox-addr, auth-id, max-amount}`, any party who observes the signature in the mempool can front-run and "consume" it themselves, permanently marking it used and causing the legitimate stacker's own broadcast transaction to fail.

### Finding Description
The `signer-sig` verification/consumption path is: [1](#0-0) 

`verify-signer-key-sig` recovers the public key from `secp256k1-recover?` over a message hash built purely from `{pox-addr, reward-cycle, topic, period, max-amount, auth-id}` — it never references `tx-sender`: [2](#0-1) 

`consume-signer-key-authorization` then marks the tuple as permanently used in `used-signer-key-authorizations`: [3](#0-2) 

and the map declaration confirms this is meant purely to prevent signature/authorization reuse "for multiple transactions": [4](#0-3) 

Because the signature does not bind the caller's identity, and the underlying `stack-stx` transaction (which embeds `signer-sig`, `signer-key`, `max-amount`, `auth-id` as ordinary Clarity arguments) is visible in the mempool before confirmation, an attacker can: [5](#0-4) 

1. Observe the victim's pending `stack-stx` transaction in the mempool, extracting `pox-addr`, `lock-period`, `signer-sig`, `signer-key`, `max-amount`, `auth-id`.
2. Submit their own `stack-stx` (or any other function sharing the same authorization tuple, e.g. `stack-extend`/`stack-increase`/`delegate-stack-stx`) call with a higher fee, using the exact same `pox-addr`, `period`, `topic`, `max-amount`, `auth-id`, `signer-key`, `signer-sig`, but their own `tx-sender` and any `amount-ustx <= max-amount`.
3. Because `verify-signer-key-sig` never checks `tx-sender`, the attacker's call succeeds and `consume-signer-key-authorization` inserts the used-tuple record.
4. When the victim's original transaction is later mined, it hits the same `consume-signer-key-authorization` call, which now fails with `ERR_SIGNER_AUTH_USED` (confirmed by tests): [6](#0-5) [7](#0-6) 

This is the direct on-chain analog of the reported ERC20 `permit()` front-running griefing: a signature meant to authorize one party's action is consumed by an unrelated party before the intended transaction lands, permanently invalidating it (the `auth-id` tuple can never be reused).

### Impact Explanation
This breaks the equality that the signer-key authorization should only be usable by/for the party the off-chain signer intended. Consuming the `auth-id` tuple is a **permanent freezing via a nonce-consuming replay**: the victim's exact stacking intent (this `pox-addr`/`reward-cycle`/`period`/`auth-id`/`max-amount` combination) can never be submitted again once consumed, since `used-signer-key-authorizations` insertion is one-way (`map-insert`, no way to un-use it). The victim must obtain a brand-new off-chain signature (a new `auth-id`) from their signer, and in the interim may miss the reward-cycle window for stacking, losing an entire cycle's opportunity to lock STX and earn rewards — a direct harm to the stacker with no requirement that the attacker gain anything (matches the "Griefing" impact class of the original report, and the "permanent freezing" bucket here).

### Likelihood Explanation
The attack requires only reading pending mempool transactions (all `stack-stx`/`stack-extend`/etc. arguments, including `signer-sig`, `signer-key`, `auth-id`, `max-amount`, are plaintext contract-call arguments, not encrypted) and broadcasting a competing transaction with a higher fee before the original is mined — the same mechanics as the original ERC20 `permit()` front-running report. No privileged access, miner cooperation, or victim key is needed; the attacker only needs their own STX to satisfy `stack-stx`'s own preconditions (sufficient balance, not already stacking/delegating) and to pay for their own lock, which is a modest cost relative to disrupting the victim.

### Recommendation
Bind the signer-key authorization payload to the intended caller/stacker principal (e.g., include `tx-sender` or an explicit `stacker` principal in `get-signer-key-message-hash`), so a signature can only be consumed by the party it was issued for. Alternatively/additionally, require that `consume-signer-key-authorization` checks that `tx-sender` (or the `stacker` argument in delegated paths) matches an expected principal recorded at signature-generation time, preventing any third party from consuming another stacker's authorization tuple.

### Proof of Concept
1. Alice (a solo stacker) obtains a `signer-sig` from her signer for `{pox-addr: P, reward-cycle: R, topic: "stack-stx", period: L, max-amount: M, auth-id: A}` and broadcasts `stack-stx(amount, P, start-burn-ht, L, signer-sig, signer-key, M, A)`.
2. Mallory observes this transaction in the mempool and extracts `(P, L, signer-sig, signer-key, M, A)`.
3. Mallory (funded with her own STX) submits her own `stack-stx(amount' <= M, P, start-burn-ht, L, signer-sig, signer-key, M, A)` with a higher fee; it is confirmed first. `consume-signer-key-authorization` succeeds and marks `{signer-key, R, L, "stack-stx", P, A, M}` as used (as validated in the referenced tests at [8](#0-7) ).
4. Alice's original transaction is then mined; `consume-signer-key-authorization` is called with the identical tuple, hits the `map-insert` failure path, and Alice's `stack-stx` call aborts with `ERR_SIGNER_AUTH_USED`, exactly as demonstrated by the "used authorization" test ( [6](#0-5) ) and the Rust integration test asserting `ERR_SIGNER_AUTH_USED` on replay ( [7](#0-6) ).
5. Alice has lost her only valid `auth-id` for this reward cycle/period/pox-addr and must obtain a new off-chain signature before she can stack again, potentially missing the current reward cycle entirely.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L248-262)
```text
;; State for tracking used signer key authorizations. This prevents re-use
;; of the same signature or pre-set authorization for multiple transactions.
;; Refer to the `signer-key-authorizations` map for the documentation on these fields
(define-map used-signer-key-authorizations
    {
        signer-key: (buff 33),
        reward-cycle: uint,
        period: uint,
        topic: (string-ascii 14),
        pox-addr: { version: (buff 1), hashbytes: (buff 32) },
        auth-id: uint,
        max-amount: uint,
    }
    bool ;; Whether the field has been used or not
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L687-709)
```text
;; Generate a message hash for validating a signer key.
;; The message hash follows SIP018 for signing structured data. The structured data
;; is the tuple `{ pox-addr: { version, hashbytes }, reward-cycle, auth-id, max-amount }`.
;; The domain is `{ name: "pox-4-signer", version: "1.0.0", chain-id: chain-id }`.
(define-read-only (get-signer-key-message-hash (pox-addr { version: (buff 1), hashbytes: (buff 32) })
                                               (reward-cycle uint)
                                               (topic (string-ascii 14))
                                               (period uint)
                                               (max-amount uint)
                                               (auth-id uint))
  (sha256 (concat
    SIP018_MSG_PREFIX
    (concat
      (sha256 (unwrap-panic (to-consensus-buff? { name: "pox-4-signer", version: "1.0.0", chain-id: chain-id })))
      (sha256 (unwrap-panic
        (to-consensus-buff? {
          pox-addr: pox-addr,
          reward-cycle: reward-cycle,
          topic: topic,
          period: period,
          auth-id: auth-id,
          max-amount: max-amount,
        })))))))
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L735-763)
```text
(define-read-only (verify-signer-key-sig (pox-addr { version: (buff 1), hashbytes: (buff 32) })
                                         (reward-cycle uint)
                                         (topic (string-ascii 14))
                                         (period uint)
                                         (signer-sig-opt (optional (buff 65)))
                                         (signer-key (buff 33))
                                         (amount uint)
                                         (max-amount uint)
                                         (auth-id uint))
  (begin
    ;; Validate that amount is less than or equal to `max-amount`
    (asserts! (>= max-amount amount) (err ERR_SIGNER_AUTH_AMOUNT_TOO_HIGH))
    (asserts! (is-none (map-get? used-signer-key-authorizations { signer-key: signer-key, reward-cycle: reward-cycle, topic: topic, period: period, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount }))
              (err ERR_SIGNER_AUTH_USED))
    (match signer-sig-opt
      ;; `signer-sig` is present, verify the signature
      signer-sig (ok (asserts!
        (is-eq
          (unwrap! (secp256k1-recover?
            (get-signer-key-message-hash pox-addr reward-cycle topic period max-amount auth-id)
            signer-sig) (err ERR_INVALID_SIGNATURE_RECOVER))
          signer-key)
        (err ERR_INVALID_SIGNATURE_PUBKEY)))
      ;; `signer-sig` is not present, verify that an authorization was previously added for this key
      (ok (asserts! (default-to false (map-get? signer-key-authorizations
            { signer-key: signer-key, reward-cycle: reward-cycle, period: period, topic: topic, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount }))
          (err ERR_NOT_ALLOWED)))
    ))
  )
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L772-788)
```text
(define-private (consume-signer-key-authorization (pox-addr { version: (buff 1), hashbytes: (buff 32) })
                                                  (reward-cycle uint)
                                                  (topic (string-ascii 14))
                                                  (period uint)
                                                  (signer-sig-opt (optional (buff 65)))
                                                  (signer-key (buff 33))
                                                  (amount uint)
                                                  (max-amount uint)
                                                  (auth-id uint))
  (begin
    ;; verify the authorization
    (try! (verify-signer-key-sig pox-addr reward-cycle topic period signer-sig-opt signer-key amount max-amount auth-id))
    ;; update the `used-signer-key-authorizations` map
    (asserts! (map-insert used-signer-key-authorizations
      { signer-key: signer-key, reward-cycle: reward-cycle, topic: topic, period: period, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount } true)
      (err ERR_SIGNER_AUTH_USED))
    (ok true)))
```

**File:** contrib/boot-contracts-unit-tests/boot_contracts/pox-4.clar (L571-605)
```text
(define-public (stack-stx (amount-ustx uint)
                          (pox-addr (tuple (version (buff 1)) (hashbytes (buff 32))))
                          (start-burn-ht uint)
                          (lock-period uint)
                          (signer-sig (optional (buff 65)))
                          (signer-key (buff 33))
                          (max-amount uint)
                          (auth-id uint))
    ;; this stacker's first reward cycle is the _next_ reward cycle
    (let ((first-reward-cycle (+ u1 (current-pox-reward-cycle)))
          (specified-reward-cycle (+ u1 (burn-height-to-reward-cycle start-burn-ht))))
      ;; the start-burn-ht must result in the next reward cycle, do not allow stackers
      ;;  to "post-date" their `stack-stx` transaction
      (asserts! (is-eq first-reward-cycle specified-reward-cycle)
                (err ERR_INVALID_START_BURN_HEIGHT))

      ;; must be called directly by the tx-sender or by an allowed contract-caller
      (asserts! (check-caller-allowed)
                (err ERR_STACKING_PERMISSION_DENIED))

      ;; tx-sender principal must not be stacking
      (asserts! (is-none (get-stacker-info tx-sender))
        (err ERR_STACKING_ALREADY_STACKED))

      ;; tx-sender must not be delegating
      (asserts! (is-none (get-check-delegation tx-sender))
        (err ERR_STACKING_ALREADY_DELEGATED))

      ;; the Stacker must have sufficient unlocked funds
      (asserts! (>= (stx-get-balance tx-sender) amount-ustx)
        (err ERR_STACKING_INSUFFICIENT_FUNDS))

      ;; Validate ownership of the given signer key
      (try! (consume-signer-key-authorization pox-addr (- first-reward-cycle u1) "stack-stx" lock-period signer-sig signer-key amount-ustx max-amount auth-id))

```

**File:** contrib/boot-contracts-unit-tests/tests/misc.test.ts (L1336-1374)
```typescript
describe("test `consume-signer-key-authorization`", () => {
  it("returns `(ok true)` for a valid signature", () => {
    const account = stackers[0];
    const amount = getStackingMinimum() * 2n;
    const maxAmount = amount * 2n;
    const poxAddr = poxAddressToTuple(account.btcAddr);
    const rewardCycle = 1;
    const period = 1;
    const authId = 1;
    const topic = Pox4SignatureTopic.AggregateCommit;
    const sigArgs = {
      authId,
      maxAmount,
      rewardCycle,
      period,
      topic,
      poxAddress: account.btcAddr,
      signerPrivateKey: account.signerPrivKey,
    };
    const signerSignature = account.client.signPoxSignature(sigArgs);

    const response = simnet.callPrivateFn(
      POX_CONTRACT,
      "consume-signer-key-authorization",
      [
        poxAddr,
        Cl.uint(rewardCycle),
        Cl.stringAscii(topic),
        Cl.uint(period),
        Cl.some(Cl.bufferFromHex(signerSignature)),
        Cl.bufferFromHex(account.signerPubKey),
        Cl.uint(amount),
        Cl.uint(maxAmount),
        Cl.uint(authId),
      ],
      address1
    );
    expect(response.result).toBeOk(Cl.bool(true));
  });
```

**File:** contrib/boot-contracts-unit-tests/tests/misc.test.ts (L1376-1430)
```typescript
  it("returns an error for a used authorization", () => {
    const account = stackers[0];
    const amount = getStackingMinimum() * 2n;
    const maxAmount = amount * 2n;
    const poxAddr = poxAddressToTuple(account.btcAddr);
    const rewardCycle = 1;
    const period = 1;
    const authId = 1;
    const topic = Pox4SignatureTopic.AggregateCommit;
    const sigArgs = {
      authId,
      maxAmount,
      rewardCycle,
      period,
      topic,
      poxAddress: account.btcAddr,
      signerPrivateKey: account.signerPrivKey,
    };
    const signerSignature = account.client.signPoxSignature(sigArgs);

    simnet.callPrivateFn(
      POX_CONTRACT,
      "consume-signer-key-authorization",
      [
        poxAddr,
        Cl.uint(rewardCycle),
        Cl.stringAscii(topic),
        Cl.uint(period),
        Cl.some(Cl.bufferFromHex(signerSignature)),
        Cl.bufferFromHex(account.signerPubKey),
        Cl.uint(amount),
        Cl.uint(maxAmount),
        Cl.uint(authId),
      ],
      address1
    );

    const response = simnet.callPrivateFn(
      POX_CONTRACT,
      "consume-signer-key-authorization",
      [
        poxAddr,
        Cl.uint(rewardCycle),
        Cl.stringAscii(topic),
        Cl.uint(period),
        Cl.some(Cl.bufferFromHex(signerSignature)),
        Cl.bufferFromHex(account.signerPubKey),
        Cl.uint(amount),
        Cl.uint(maxAmount),
        Cl.uint(authId),
      ],
      address1
    );
    expect(response.result).toBeErr(Cl.int(ERRORS.ERR_SIGNER_AUTH_USED));
  });
```

**File:** stackslib/src/chainstate/stacks/boot/pox_4_tests.rs (L7356-7367)
```rust
    assert_eq!(alice_replay_result, Value::Int(35));

    // Check Bob replay, expect (err 19) - ERR_SIGNER_AUTH_USED
    let bob_tx_result = receipts
        .get(2)
        .unwrap()
        .result
        .clone()
        .expect_result_err()
        .unwrap();
    assert_eq!(bob_tx_result, Value::Int(19));
}
```
