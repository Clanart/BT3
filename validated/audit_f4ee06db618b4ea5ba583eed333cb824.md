Looking at the external bug pattern — a function iterates over a collection, assumes a data source exists for every element, and fails when one element has no corresponding data source — I need to find the nearcore analog in chunk/state/witness reconstruction, state sync parts, or resharding.

Let me trace the exact code path.