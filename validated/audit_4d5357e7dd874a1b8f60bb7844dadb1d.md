[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L466-475)
```text
(define-private (get-position (account principal)) ;; enabled only
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))

(define-private (get-full-position (account principal)) ;; all collaterals
  (contract-call? .v0-market-vault get-position account MAX-U64))

(define-private (get-liquidation-position (account principal)) ;; liquidation specific (enabled collateral + all debt)
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1123-1153)
```text
              (pos-full (if is-collateral-enabled position (try! (get-full-position account))))
              (u-debt (accrue-user-debts (get debt pos-full)))
              (u-coll (accrue-user-collateral (get collateral pos-full)))
              (assets (get-assets position-mask))
              (curr-coll-aid (find-collateral-amount (get collateral position) asset-id))
              (removing-all (is-eq amount curr-coll-aid))
              (current-group (try! (get-egroup position-mask)))
              (current-ltvb (buff-to-uint-be (get LTV-BORROW current-group)))
              (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
              (collateral-value (get collateral notional-valued-assets))
              (debt-value (get debt notional-valued-assets))
              (removed-asset-value (find-and-resolve-asset-value assets asset-id amount true)))

          (asserts! (is-healthy collateral-value debt-value current-ltvb) ERR-UNHEALTHY)
          (asserts!
            (if is-collateral-enabled
                (let ((t (asserts! (>= collateral-value removed-asset-value) ERR-INSUFFICIENT-COLLATERAL))
                      (post-removal-collateral-value (- collateral-value removed-asset-value)))
                  (if removing-all
                      (let ((future-mask (bit-and position-mask (bit-not (pow u2 asset-id)))))
                        (try! (is-healthy-with-mask post-removal-collateral-value debt-value future-mask)))
                      (is-healthy post-removal-collateral-value debt-value current-ltvb)))
                (let ((oracle-data (get oracle asset))
                      (price (unwrap! (price-resolve oracle-data) ERR-DISABLED-COLLATERAL-PRICE-FAILED))
                      (decimals (get decimals asset))
                      (user-amount (find-collateral-amount (get collateral pos-full) asset-id))
                      (disabled-notional (normalize (* user-amount price) decimals false))
                      (removal-notional (normalize (* amount price) decimals true))
                      (total-collateral-value (+ collateral-value disabled-notional)))
                  (asserts! (>= total-collateral-value removal-notional) ERR-INSUFFICIENT-COLLATERAL)
                  (is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1186-1206)
```text
    (try! (contract-call? ft transfer amount account current-contract none))
    
    ;; Step 2: Deposit to vault to get zTokens (minted to user)
    ;; Now the market has the underlying tokens and can call vault-deposit
    (let ((shares-minted 
            (try! (if (is-eq ft-address ZEST-STX-WRAPPER-CONTRACT)
              ;; For wSTX: use as-contract with-stx pattern
              (as-contract? ((with-stx amount))
                (try! (vault-deposit asset-id amount min-shares account)))
              ;; For other tokens: use as-contract with-ft pattern
              (as-contract? ((with-ft ft-address "*" amount))
                (try! (vault-deposit asset-id amount min-shares account)))))))
      
      ;; Step 3: Add the minted zTokens as collateral
      (if (is-eq asset-id STX) (collateral-add .v0-vault-stx shares-minted price-feeds)
      (if (is-eq asset-id sBTC) (collateral-add .v0-vault-sbtc shares-minted price-feeds)
      (if (is-eq asset-id stSTX) (collateral-add .v0-vault-ststx shares-minted price-feeds)
      (if (is-eq asset-id USDC) (collateral-add .v0-vault-usdc shares-minted price-feeds)
      (if (is-eq asset-id USDH) (collateral-add .v0-vault-usdh shares-minted price-feeds)
      (if (is-eq asset-id stSTXbtc) (collateral-add .v0-vault-ststxbtc shares-minted price-feeds)
      ERR-UNKNOWN-VAULT))))))))
```
