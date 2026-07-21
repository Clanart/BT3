Looking at the external bug — a credit account retaining a stale reference to its previous credit manager after being returned to the factory — the sequencer analog is: after a block is **reverted** (returned), a cached commitment field still points to the reverted block's data instead of being reset.

Let me trace the exact code path.